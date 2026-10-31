"""One-tap order approval.   Nothing is ever sent to a broker unless you approve that specific order.

    python -m app.execution status
    python -m app.execution propose          # turn the latest recommendations into PENDING order tickets
    python -m app.execution list
    python -m app.execution approve 12       # send ticket 12 to the broker (paper account unless you enabled live)
    python -m app.execution reject 12
    python -m app.execution cancel 12        # cancel a submitted entry order that has not filled
    python -m app.execution halt | resume    # kill switch

Flow: recommendations -> tickets (PENDING, sized and checked) -> you approve -> broker. Approval re-checks every limit
at that moment, so the limits hold whether you approve from the command line or the website.

Limits (config / .env): largest single order, approved orders per day, approved dollars per day, ticket expiry, kill
switch. Buys use whole shares with an attached stop-loss and take-profit. Sells close a position you already hold.
"""
from __future__ import annotations

import argparse
import hmac
import json
import logging
import math
from datetime import datetime, timedelta, timezone

from . import config, db
from .broker import OrderRequest, get_broker

log = logging.getLogger("execution")


# --------------------------------------------------------------------------- small helpers
def check_token(provided: str | None, expected: str) -> str | None:
    """None if OK, otherwise the reason approvals over HTTP are refused."""
    if not expected:
        return "Approving over the website is switched off. Set STOCKBOT_APPROVE_TOKEN, or approve from the command line."
    if not hmac.compare_digest((provided or "").encode(), expected.encode()):
        return "Wrong approval token."
    return None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _manual() -> bool:
    return config.BROKER in ("none", "manual")


def _halted(conn) -> bool:
    conn.execute("INSERT OR IGNORE INTO exec_state(id,halted) VALUES(1,0)")
    return bool(conn.execute("SELECT halted FROM exec_state WHERE id=1").fetchone()["halted"])


def is_halted() -> bool:
    db.init_db()
    with db.connect() as conn:
        return _halted(conn)


def set_halted(halted: bool) -> None:
    db.init_db()
    with db.connect() as conn:
        conn.execute("INSERT OR IGNORE INTO exec_state(id,halted) VALUES(1,0)")
        conn.execute("UPDATE exec_state SET halted=? WHERE id=1", (int(halted),))
    log.warning("Order approvals %s", "HALTED (kill switch on)" if halted else "resumed")


def _today_usage(conn, now: datetime) -> tuple[int, float]:
    day = now.strftime("%Y-%m-%d")
    rows = conn.execute("SELECT notional FROM order_tickets WHERE status='SUBMITTED' AND substr(decided,1,10)=?", (day,)).fetchall()
    return len(rows), float(sum(r["notional"] for r in rows))


def _expire_old(conn, now: datetime) -> None:
    cutoff = (now - timedelta(hours=config.TICKET_TTL_HOURS)).isoformat()
    conn.execute("UPDATE order_tickets SET status='EXPIRED', decided=?, message='Not approved in time' WHERE status='PENDING' AND created < ?",
                 (now.isoformat(), cutoff))


# --------------------------------------------------------------------------- status
def status(now: datetime | None = None) -> dict:
    db.init_db()
    now = now or _now()
    out = {"broker": config.BROKER, "mode": "manual", "halted": is_halted(), "connected": False, "message": "",
           "limits": {"max_order_usd": config.MAX_ORDER_USD, "max_daily_orders": config.MAX_DAILY_ORDERS,
                      "max_daily_usd": config.MAX_DAILY_USD, "ticket_ttl_hours": config.TICKET_TTL_HOURS},
           "web_approval_enabled": bool(config.APPROVE_TOKEN)}
    with db.connect() as conn:
        _expire_old(conn, now)
        n, usd = _today_usage(conn, now)
        out["today"] = {"orders": n, "usd": usd}
        out["pending"] = conn.execute("SELECT COUNT(*) c FROM order_tickets WHERE status='PENDING'").fetchone()["c"]
    if _manual():
        out["message"] = "Manual mode: place trades yourself in your broker app. Set STOCKBOT_BROKER=alpaca to enable approvals."
        return out
    try:
        b = get_broker()
        out["mode"], out["connected"] = b.mode, True
        out["equity"], out["cash"] = b.get_equity(), b.get_cash()
    except Exception as e:
        out["message"] = f"Broker not reachable: {e}"
    return out


# --------------------------------------------------------------------------- propose
def propose(now: datetime | None = None) -> dict:
    """Create PENDING tickets from the latest recommendation batch. Never submits anything."""
    db.init_db()
    now = now or _now()
    if _manual():
        return {"created": 0, "skipped": [], "note": "Manual mode: no tickets are created"}
    broker = get_broker()
    equity = config.ACCOUNT_SIZE or broker.get_equity()
    held = {p.symbol: p for p in broker.get_positions()}
    created, skipped = 0, []
    with db.connect() as conn:
        _expire_old(conn, now)
        batch = conn.execute("SELECT MAX(batch) b FROM recommendations").fetchone()["b"]
        recs = conn.execute("SELECT * FROM recommendations WHERE batch=? ORDER BY ABS(combined) DESC", (batch,)).fetchall() if batch else []
        for r in recs:
            sym = r["symbol"]
            if not broker.supports(sym):
                skipped.append(f"{sym}: {broker.name} cannot trade this market")
                continue
            bsym = sym.replace("-", ".")
            if conn.execute("SELECT 1 FROM order_tickets WHERE rec_id=? AND side=?", (r["id"], "buy" if r["action"] == "BUY" else "sell")).fetchone():
                continue                                   # already proposed
            if conn.execute("SELECT 1 FROM order_tickets WHERE symbol=? AND status IN ('PENDING','SUBMITTED') AND side='buy'", (sym,)).fetchone() and r["action"] == "BUY":
                skipped.append(f"{sym}: an order for it is already open")
                continue
            if r["action"] == "BUY":
                if bsym in held:
                    skipped.append(f"{sym}: already held")
                    continue
                budget = min(equity * r["size_pct"] / 100, config.MAX_ORDER_USD)
                qty = math.floor(budget / r["entry"])
                if qty < 1:
                    skipped.append(f"{sym}: one share costs more than the order limit")
                    continue
                side, limit, stop, target, notional = "buy", r["entry"], r["stop"], r["target"], qty * r["entry"]
            else:
                if bsym not in held:
                    continue                              # SELL on something you do not hold: nothing to do
                qty = math.floor(held[bsym].quantity)
                if qty < 1:
                    continue
                side, limit, stop, target, notional = "sell", None, None, None, qty * r["entry"]
            conn.execute("INSERT INTO order_tickets(rec_id,symbol,side,qty,limit_price,stop,target,notional,broker,mode,created) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                         (r["id"], sym, side, qty, limit, stop, target, notional, broker.name, broker.mode, now.isoformat()))
            created += 1
    log.info("Proposed %d order tickets (%d skipped)", created, len(skipped))
    return {"created": created, "skipped": skipped}


# --------------------------------------------------------------------------- approve / reject / cancel
def _fail(msg: str) -> dict:
    return {"ok": False, "error": msg}


def approve(ticket_id: int, now: datetime | None = None) -> dict:
    """Send one ticket to the broker after re-checking every limit."""
    db.init_db()
    now = now or _now()
    if _manual():
        return _fail("Manual mode: there is no broker connected. Set STOCKBOT_BROKER=alpaca.")
    with db.connect() as conn:
        _expire_old(conn, now)
        t = conn.execute("SELECT * FROM order_tickets WHERE id=?", (ticket_id,)).fetchone()
        if not t:
            return _fail(f"No ticket {ticket_id}")
        if t["status"] != "PENDING":
            return _fail(f"Ticket {ticket_id} is {t['status']}, not pending")
        if _halted(conn):
            return _fail("Kill switch is on: no orders can be approved. Run `python -m app.execution resume` to turn it off.")
        n, usd = _today_usage(conn, now)
        if n >= config.MAX_DAILY_ORDERS:
            return _fail(f"Daily limit reached: {n} orders already approved today (max {config.MAX_DAILY_ORDERS})")
        if usd + t["notional"] > config.MAX_DAILY_USD:
            return _fail(f"Daily dollar limit: {usd:,.0f} approved today, this order adds {t['notional']:,.0f} (max {config.MAX_DAILY_USD:,.0f})")
        if t["notional"] > config.MAX_ORDER_USD * 1.0001:
            return _fail(f"Order is {t['notional']:,.0f}, above the single-order limit of {config.MAX_ORDER_USD:,.0f}")
        try:
            broker = get_broker()
            if broker.mode != t["mode"]:
                return _fail(f"The broker mode changed from {t['mode']} to {broker.mode} since this ticket was made. Reject it and propose again.")
            if t["side"] == "buy" and broker.get_cash() < t["notional"]:
                return _fail("Not enough cash in the broker account for this order")
        except Exception as e:
            return _fail(f"Broker not reachable: {e}")

        res = broker.place_order(OrderRequest(symbol=t["symbol"], side=t["side"], quantity=t["qty"], order_type="limit" if t["limit_price"] else "market",
                                              limit_price=t["limit_price"], stop_loss=t["stop"], take_profit=t["target"], recommendation_id=t["rec_id"]))
        conn.execute("UPDATE order_tickets SET status=?, decided=?, broker_order_id=?, message=? WHERE id=?",
                     ("SUBMITTED" if res.accepted else "FAILED", now.isoformat(), res.order_id, res.message, ticket_id))
    log.info("Ticket %s %s: %s", ticket_id, "SUBMITTED" if res.accepted else "FAILED", res.message)
    return {"ok": res.accepted, "message": res.message, "error": None if res.accepted else res.message, "mode": broker.mode}


def reject(ticket_id: int, now: datetime | None = None) -> dict:
    now = now or _now()
    db.init_db()
    with db.connect() as conn:
        cur = conn.execute("UPDATE order_tickets SET status='REJECTED', decided=?, message='Rejected by you' WHERE id=? AND status='PENDING'",
                           (now.isoformat(), ticket_id))
    return {"ok": cur.rowcount == 1, "error": None if cur.rowcount == 1 else f"Ticket {ticket_id} is not pending"}


def cancel(ticket_id: int, now: datetime | None = None) -> dict:
    """Cancel a submitted order that has not filled yet."""
    now = now or _now()
    db.init_db()
    with db.connect() as conn:
        t = conn.execute("SELECT * FROM order_tickets WHERE id=?", (ticket_id,)).fetchone()
        if not t or t["status"] != "SUBMITTED" or not t["broker_order_id"]:
            return _fail("Only a submitted order can be cancelled")
        if _manual() or not get_broker().cancel_order(t["broker_order_id"]):
            return _fail("The broker did not cancel it (it may already be filled). Check your broker app.")
        conn.execute("UPDATE order_tickets SET status='CANCELLED', decided=?, message='Cancelled by you' WHERE id=?", (now.isoformat(), ticket_id))
    return {"ok": True, "error": None}


def list_tickets(limit: int = 50, now: datetime | None = None) -> list[dict]:
    db.init_db()
    with db.connect() as conn:
        _expire_old(conn, now or _now())
        rows = conn.execute("SELECT * FROM order_tickets ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [{"id": r["id"], "symbol": r["symbol"], "side": r["side"], "qty": r["qty"], "limitPrice": r["limit_price"], "stop": r["stop"],
             "target": r["target"], "notional": r["notional"], "status": r["status"], "mode": r["mode"], "message": r["message"] or "",
             "created": r["created"], "decided": r["decided"]} for r in rows]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["status", "propose", "list", "approve", "reject", "cancel", "halt", "resume"])
    ap.add_argument("ticket", nargs="?", type=int)
    a = ap.parse_args()
    if a.cmd in ("approve", "reject", "cancel") and a.ticket is None:
        ap.error(f"{a.cmd} needs a ticket number")
    if a.cmd == "halt":
        set_halted(True)
    elif a.cmd == "resume":
        set_halted(False)
    else:
        fn = {"status": status, "propose": propose, "list": list_tickets}.get(a.cmd)
        out = fn() if fn else {"approve": approve, "reject": reject, "cancel": cancel}[a.cmd](a.ticket)
        print(json.dumps(out, indent=2, default=str))
