"""Paper (simulated) portfolio that follows the engine's recommendations, with hard risk limits.

    python -m app.portfolio sync      # update positions, apply new BUY recommendations
    python -m app.portfolio status
    python -m app.portfolio pause     # kill switch: no new entries
    python -m app.portfolio resume

Rules
- Long-only: BUY recommendations open positions; a newer SELL recommendation on a held stock closes it
  ("exit signal"). Short selling is not simulated.
- Exits use the same rules as the backtest (trades.py): stop / target / 20-day timeout, 0.1% cost per side.
- Limits: max 5% of equity per stock, max 25% per sector, keep 10% cash, max 20 positions, and no new entries
  on a day the portfolio is down 5% or more from the previous day's equity.
- Positions are tracked as notional amounts in one base currency. FX moves between USD / AUD / INR are ignored.
"""
from __future__ import annotations

import argparse
import logging
import os
from datetime import datetime, timezone

from . import db, trades
from .data import prices as price_data

log = logging.getLogger("portfolio")

START_CASH = float(os.getenv("STOCKBOT_PAPER_CASH", "100000"))
LIMITS = {"max_position_pct": 5.0, "max_sector_pct": 25.0, "min_cash_pct": 10.0, "max_positions": 20, "daily_dd_stop_pct": 5.0}
MIN_TRADE_PCT = 0.2   # ignore entries smaller than 0.2% of equity


def _ensure(conn) -> None:
    conn.execute("INSERT OR IGNORE INTO paper_state(id,cash,start_cash,paused,created) VALUES(1,?,?,0,?)",
                 (START_CASH, START_CASH, datetime.now(timezone.utc).isoformat()))


def _last_close(symbol: str) -> float | None:
    df = price_data.get_prices(symbol)
    return float(df["close"].iloc[-1]) if len(df) else None


def _position_value(t, last: float | None) -> float:
    if last is None:
        return t["notional"]
    return t["notional"] * (1 + trades.pnl_pct("BUY", t["entry"], last) / 100)


def _equity(conn) -> float:
    cash = conn.execute("SELECT cash FROM paper_state WHERE id=1").fetchone()["cash"]
    return cash + sum(_position_value(t, _last_close(t["symbol"])) for t in conn.execute("SELECT * FROM paper_trades WHERE status='OPEN'").fetchall())


def _close(conn, t, exit_price: float, reason: str, when: str) -> None:
    pnl = trades.pnl_pct("BUY", t["entry"], exit_price)
    conn.execute("UPDATE paper_trades SET status='CLOSED', closed=?, exit_price=?, exit_reason=?, pnl_pct=? WHERE id=?",
                 (when, exit_price, reason, pnl, t["id"]))
    conn.execute("UPDATE paper_state SET cash = cash + ? WHERE id=1", (t["notional"] * (1 + pnl / 100),))


def sync(today: str | None = None) -> dict:
    db.init_db()
    today = today or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out = {"opened": 0, "closed": 0, "skipped": [], "halted": False, "paused": False}
    with db.connect() as conn:
        _ensure(conn)

        # 1) close positions that hit stop / target / timeout
        for t in conn.execute("SELECT * FROM paper_trades WHERE status='OPEN'").fetchall():
            fut = price_data.get_prices(t["symbol"])
            fut = fut[fut.index > t["opened"][:10]]
            if fut.empty:
                continue
            res = trades.resolve("BUY", t["stop"], t["target"], fut["high"].to_numpy(), fut["low"].to_numpy(), fut["close"].to_numpy())
            if res:
                price, reason, used = res
                _close(conn, t, price, reason, fut.index[used - 1].strftime("%Y-%m-%d"))
                out["closed"] += 1

        # 2) exit signals: a newer SELL recommendation on a stock we hold
        for t in conn.execute("SELECT * FROM paper_trades WHERE status='OPEN'").fetchall():
            sell = conn.execute("SELECT id FROM recommendations WHERE symbol=? AND action='SELL' AND created > ? LIMIT 1",
                                (t["symbol"], t["opened"])).fetchone()
            last = _last_close(t["symbol"])
            if sell and last:
                _close(conn, t, last, "exit_signal", today)
                out["closed"] += 1

        # 3) open new positions from the latest batch, subject to limits
        equity = _equity(conn)
        paused = bool(conn.execute("SELECT paused FROM paper_state WHERE id=1").fetchone()["paused"])
        prev = conn.execute("SELECT value FROM paper_equity WHERE date < ? ORDER BY date DESC LIMIT 1", (today,)).fetchone()
        halted = bool(prev) and (equity / prev["value"] - 1) * 100 <= -LIMITS["daily_dd_stop_pct"]
        out["halted"], out["paused"] = halted, paused
        if halted or paused:
            out["skipped"].append("Daily loss limit hit: no new entries today" if halted else "Paused (kill switch): no new entries")
        else:
            batch = conn.execute("SELECT MAX(batch) b FROM recommendations").fetchone()["b"]
            recs = conn.execute(
                "SELECT r.*, t.sector FROM recommendations r LEFT JOIN tickers t ON t.symbol=r.symbol "
                "WHERE r.batch=? AND r.action='BUY' AND r.id NOT IN (SELECT rec_id FROM paper_trades WHERE rec_id IS NOT NULL) "
                "ORDER BY r.strength DESC", (batch,)).fetchall() if batch else []
            for r in recs:
                held = conn.execute("SELECT 1 FROM paper_trades WHERE status='OPEN' AND symbol=?", (r["symbol"],)).fetchone()
                n_open = conn.execute("SELECT COUNT(*) c FROM paper_trades WHERE status='OPEN'").fetchone()["c"]
                if held or n_open >= LIMITS["max_positions"]:
                    out["skipped"].append(f"{r['symbol']}: {'already held' if held else 'max positions reached'}")
                    continue
                sector = r["sector"] or "Unknown"
                cash = conn.execute("SELECT cash FROM paper_state WHERE id=1").fetchone()["cash"]
                sector_used = sum(t["notional"] for t in conn.execute("SELECT notional FROM paper_trades WHERE status='OPEN' AND sector IS ?", (sector,)))
                notional = min(equity * min(r["size_pct"], LIMITS["max_position_pct"]) / 100,
                               equity * LIMITS["max_sector_pct"] / 100 - sector_used,
                               cash - equity * LIMITS["min_cash_pct"] / 100)
                if notional < equity * MIN_TRADE_PCT / 100:
                    out["skipped"].append(f"{r['symbol']}: no room (sector cap or cash reserve)")
                    continue
                conn.execute("INSERT INTO paper_trades(rec_id,symbol,sector,notional,entry,stop,target,opened) VALUES(?,?,?,?,?,?,?,?)",
                             (r["id"], r["symbol"], sector, notional, r["entry"], r["stop"], r["target"], r["created"]))
                conn.execute("UPDATE paper_state SET cash = cash - ? WHERE id=1", (notional,))
                out["opened"] += 1

        # 4) record today's equity
        conn.execute("INSERT OR REPLACE INTO paper_equity(date,value) VALUES(?,?)", (today, _equity(conn)))
    log.info("Paper portfolio synced: %s", out)
    return out


def set_paused(paused: bool) -> None:
    db.init_db()
    with db.connect() as conn:
        _ensure(conn)
        conn.execute("UPDATE paper_state SET paused=? WHERE id=1", (int(paused),))


def snapshot() -> dict:
    """Shape expected by the website: cash, positions (qty / avgPrice / last) and an equity curve."""
    db.init_db()
    with db.connect() as conn:
        _ensure(conn)
        st = conn.execute("SELECT * FROM paper_state WHERE id=1").fetchone()
        positions = []
        for t in conn.execute("SELECT * FROM paper_trades WHERE status='OPEN' ORDER BY opened").fetchall():
            last = _last_close(t["symbol"]) or t["entry"]
            positions.append({"symbol": t["symbol"], "qty": round(t["notional"] / t["entry"], 4), "avgPrice": t["entry"], "last": last,
                              "sector": t["sector"] or "Unknown", "notional": t["notional"], "value": _position_value(t, last),
                              "stop": t["stop"], "target": t["target"], "opened": t["opened"][:10]})
        eq = [{"date": r["date"], "value": r["value"]} for r in conn.execute("SELECT date,value FROM paper_equity ORDER BY date")]
        closed = conn.execute("SELECT pnl_pct, notional FROM paper_trades WHERE status='CLOSED'").fetchall()
        wins = sum(1 for c in closed if c["pnl_pct"] > 0)
        return {"cash": st["cash"], "start_cash": st["start_cash"], "paused": bool(st["paused"]), "positions": positions, "equity": eq,
                "closed_trades": len(closed), "win_rate": wins / len(closed) if closed else None,
                "realised_pnl": sum(c["notional"] * c["pnl_pct"] / 100 for c in closed), "limits": LIMITS}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["sync", "status", "pause", "resume"])
    a = ap.parse_args()
    if a.cmd == "sync":
        print(sync())
    elif a.cmd == "pause":
        set_paused(True); print("Paused: no new paper entries.")
    elif a.cmd == "resume":
        set_paused(False); print("Resumed.")
    else:
        print(snapshot())
