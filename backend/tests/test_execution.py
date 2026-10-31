"""Broker adapter (Alpaca, with a fake HTTP session) and the approval workflow with its limits."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from app import config, db, execution
from app.broker import OrderRequest
from app.broker.alpaca import AlpacaBroker
from app.broker.base import Broker, OrderResult, Position

NOW = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)


# ------------------------------------------------------------------ fake HTTP for Alpaca
class Resp:
    def __init__(self, status=200, data=None):
        self.status_code, self._data = status, data if data is not None else {}
        self.ok, self.text = status < 400, json.dumps(self._data) if data is not None else ""

    def json(self):
        return self._data


class FakeSession:
    def __init__(self, routes=None):
        self.headers, self.calls, self.routes = {}, [], routes or {}

    def request(self, method, url, timeout=None, json=None):
        self.calls.append((method, url, json))
        return self.routes.get((method, url.split(".markets")[1]), Resp(200, {}))


@pytest.fixture(autouse=True)
def cfg(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "e.db"))
    monkeypatch.setattr(config, "ALPACA_KEY", "k")
    monkeypatch.setattr(config, "ALPACA_SECRET", "s")
    monkeypatch.setattr(config, "ALPACA_LIVE", False)
    monkeypatch.setattr(config, "MAX_ORDER_USD", 1000.0)
    monkeypatch.setattr(config, "MAX_DAILY_ORDERS", 5)
    monkeypatch.setattr(config, "MAX_DAILY_USD", 3000.0)
    monkeypatch.setattr(config, "ACCOUNT_SIZE", 0.0)
    monkeypatch.setattr(config, "TICKET_TTL_HOURS", 24)
    db.init_db()


# ------------------------------------------------------------------ Alpaca adapter
def test_alpaca_paper_by_default_and_headers():
    b = AlpacaBroker(FakeSession())
    assert b.mode == "paper" and b.base == "https://paper-api.alpaca.markets"
    assert b.session.headers == {"APCA-API-KEY-ID": "k", "APCA-API-SECRET-KEY": "s"}


def test_alpaca_live_needs_explicit_switch(monkeypatch):
    monkeypatch.setattr(config, "ALPACA_LIVE", True)
    b = AlpacaBroker(FakeSession())
    assert b.mode == "live" and b.base == "https://api.alpaca.markets"


def test_alpaca_requires_keys(monkeypatch):
    monkeypatch.setattr(config, "ALPACA_KEY", "")
    with pytest.raises(ValueError):
        AlpacaBroker(FakeSession())


def test_alpaca_bracket_order_body():
    s = FakeSession({("POST", "/v2/orders"): Resp(200, {"id": "abc-123"})})
    r = AlpacaBroker(s).place_order(OrderRequest("BRK-B", "buy", 3, "limit", 100.0, stop_loss=94.0, take_profit=109.0))
    assert r.accepted and r.order_id == "abc-123"
    method, url, body = s.calls[-1]
    assert (method, url) == ("POST", "https://paper-api.alpaca.markets/v2/orders")
    assert body == {"symbol": "BRK.B", "qty": "3", "side": "buy", "type": "limit", "time_in_force": "gtc", "limit_price": "100.00",
                    "order_class": "bracket", "take_profit": {"limit_price": "109.00"}, "stop_loss": {"stop_price": "94.00"}}


@pytest.mark.parametrize("kw,why", [
    (dict(symbol="BHP.AX"), "US stocks only"),
    (dict(symbol="INFY.NS"), "US stocks only"),
    (dict(quantity=1.5), "whole number"),
    (dict(quantity=0), "whole number"),
    (dict(stop_loss=None), "needs a limit price"),
    (dict(stop_loss=101.0), "stop-loss < entry < target"),
    (dict(take_profit=99.0), "stop-loss < entry < target"),
])
def test_alpaca_refuses_bad_orders(kw, why):
    s = FakeSession()
    base = dict(symbol="AAPL", side="buy", quantity=2, order_type="limit", limit_price=100.0, stop_loss=94.0, take_profit=109.0)
    r = AlpacaBroker(s).place_order(OrderRequest(**{**base, **kw}))
    assert not r.accepted and why in r.message and not s.calls     # refused locally, nothing sent


def test_alpaca_sell_closes_only_held_position():
    s = FakeSession({("GET", "/v2/positions"): Resp(200, [{"symbol": "AAPL", "qty": "5", "avg_entry_price": "190"}]),
                     ("DELETE", "/v2/positions/AAPL"): Resp(200, {"id": "close-1"})})
    b = AlpacaBroker(s)
    assert b.place_order(OrderRequest("AAPL", "sell", 5)).accepted
    assert not b.place_order(OrderRequest("MSFT", "sell", 5)).accepted
    assert [c[0:2] for c in s.calls if c[0] == "DELETE"] == [("DELETE", "https://paper-api.alpaca.markets/v2/positions/AAPL")]


def test_alpaca_api_error_becomes_failed_result():
    s = FakeSession({("POST", "/v2/orders"): Resp(403, {"message": "insufficient buying power"})})
    r = AlpacaBroker(s).place_order(OrderRequest("AAPL", "buy", 1, "limit", 100.0, stop_loss=94.0, take_profit=109.0))
    assert not r.accepted and "403" in r.message


# ------------------------------------------------------------------ approval workflow with a fake broker
class FakeBroker(Broker):
    name, mode = "fake", "paper"

    def __init__(self, equity=100000.0, cash=100000.0, held=None):
        self.equity, self.cash, self.held, self.orders, self.cancelled = equity, cash, held or {}, [], []

    def supports(self, symbol):
        return not symbol.endswith((".AX", ".NS"))

    def get_equity(self): return self.equity
    def get_cash(self): return self.cash
    def get_positions(self): return [Position(s, q, 100.0) for s, q in self.held.items()]

    def place_order(self, order):
        self.orders.append(order)
        return OrderResult(True, order_id=f"id{len(self.orders)}", message="ok")

    def cancel_order(self, order_id):
        self.cancelled.append(order_id)
        return True


@pytest.fixture
def broker(monkeypatch):
    b = FakeBroker()
    monkeypatch.setattr(config, "BROKER", "fake")
    monkeypatch.setattr(execution, "get_broker", lambda: b)
    return b


def add_rec(sym, action="BUY", entry=100.0, size=4.0, batch="2026-10-29T08:30", combined=0.7):
    stop, target = (entry * 0.94, entry * 1.09) if action == "BUY" else (entry * 1.06, entry * 0.91)
    with db.connect() as conn:
        conn.execute("INSERT INTO recommendations(batch,created,symbol,action,entry,target,stop,size_pct,strength,combined,model_version) "
                     "VALUES(?,?,?,?,?,?,?,?,?,?,?)", (batch, NOW.isoformat(), sym, action, entry, target, stop, size, 0.7, combined, "v1"))


def tickets(status=None):
    return [t for t in execution.list_tickets(now=NOW) if status is None or t["status"] == status]


def test_manual_mode_creates_and_sends_nothing(monkeypatch):
    monkeypatch.setattr(config, "BROKER", "none")
    add_rec("AAPL")
    assert execution.propose(now=NOW)["created"] == 0 and tickets() == []
    assert "Manual mode" in execution.approve(1, now=NOW)["error"]
    assert execution.status(NOW)["mode"] == "manual"


def test_propose_sizes_and_caps_orders(broker):
    add_rec("AAPL", entry=100.0, size=4.0)            # 4% of 100k = 4000, capped at 1000 -> 10 shares
    add_rec("TSLA", entry=2500.0, size=4.0)           # one share costs more than the 1000 limit -> skipped
    add_rec("BHP.AX")                                 # Alpaca cannot trade ASX -> skipped
    out = execution.propose(now=NOW)
    assert out["created"] == 1 and len(out["skipped"]) == 2
    t = tickets("PENDING")[0]
    assert (t["symbol"], t["side"], t["qty"], t["limitPrice"]) == ("AAPL", "buy", 10, 100.0)
    assert t["stop"] == pytest.approx(94.0) and t["target"] == pytest.approx(109.0)
    assert execution.propose(now=NOW)["created"] == 0   # not proposed twice


def test_propose_skips_held_and_makes_exit_tickets(broker):
    broker.held = {"AAPL": 7.0, "MSFT": 4.0}
    add_rec("AAPL", "BUY")                             # already held -> skipped
    add_rec("MSFT", "SELL", entry=400.0)               # held -> exit ticket for the full position
    add_rec("NVDA", "SELL", entry=140.0)               # not held -> nothing to do
    execution.propose(now=NOW)
    t = tickets()
    assert [(x["symbol"], x["side"], x["qty"]) for x in t] == [("MSFT", "sell", 4)]


def test_approve_sends_exactly_once(broker):
    add_rec("AAPL")
    execution.propose(now=NOW)
    r = execution.approve(1, now=NOW)
    assert r["ok"] and len(broker.orders) == 1 and tickets()[0]["status"] == "SUBMITTED"
    o = broker.orders[0]
    assert (o.symbol, o.side, o.quantity, o.limit_price) == ("AAPL", "buy", 10, 100.0) and o.stop_loss and o.take_profit
    assert not execution.approve(1, now=NOW)["ok"] and len(broker.orders) == 1      # second approval refused


def test_reject_and_unknown_ticket(broker):
    add_rec("AAPL")
    execution.propose(now=NOW)
    assert execution.reject(1, now=NOW)["ok"] and not broker.orders
    assert not execution.approve(1, now=NOW)["ok"]
    assert "No ticket" in execution.approve(99, now=NOW)["error"]


def test_tickets_expire(broker):
    add_rec("AAPL")
    execution.propose(now=NOW)
    r = execution.approve(1, now=NOW + timedelta(hours=25))
    assert not r["ok"] and "EXPIRED" in r["error"] and not broker.orders


def test_kill_switch_blocks_approval(broker):
    add_rec("AAPL")
    execution.propose(now=NOW)
    execution.set_halted(True)
    assert "Kill switch" in execution.approve(1, now=NOW)["error"] and not broker.orders
    execution.set_halted(False)
    assert execution.approve(1, now=NOW)["ok"]


def test_daily_order_and_dollar_limits(broker, monkeypatch):
    for i in range(4):
        add_rec(f"S{i}")                              # four 1000-dollar tickets
    execution.propose(now=NOW)
    monkeypatch.setattr(config, "MAX_DAILY_ORDERS", 2)
    assert execution.approve(1, now=NOW)["ok"] and execution.approve(2, now=NOW)["ok"]
    assert "Daily limit" in execution.approve(3, now=NOW)["error"]
    monkeypatch.setattr(config, "MAX_DAILY_ORDERS", 10)
    monkeypatch.setattr(config, "MAX_DAILY_USD", 2500.0)                              # 2000 used, +1000 would exceed
    assert "dollar limit" in execution.approve(3, now=NOW)["error"]
    assert len(broker.orders) == 2
    monkeypatch.setattr(config, "TICKET_TTL_HOURS", 72)
    assert execution.approve(3, now=NOW + timedelta(days=1))["ok"]                    # a new UTC day: the daily counters reset
    assert len(broker.orders) == 3


def test_not_enough_cash_and_mode_change(broker):
    add_rec("AAPL")
    execution.propose(now=NOW)
    broker.cash = 500.0
    assert "Not enough cash" in execution.approve(1, now=NOW)["error"] and not broker.orders
    broker.cash, broker.mode = 100000.0, "live"
    assert "mode changed" in execution.approve(1, now=NOW)["error"] and not broker.orders


def test_failed_order_is_recorded_not_retried(broker, monkeypatch):
    add_rec("AAPL")
    execution.propose(now=NOW)
    monkeypatch.setattr(broker, "place_order", lambda o: OrderResult(False, message="insufficient buying power"))
    r = execution.approve(1, now=NOW)
    assert not r["ok"] and tickets()[0]["status"] == "FAILED" and "insufficient" in tickets()[0]["message"]


def test_cancel_submitted_order(broker):
    add_rec("AAPL")
    execution.propose(now=NOW)
    execution.approve(1, now=NOW)
    assert execution.cancel(1, now=NOW)["ok"] and broker.cancelled == ["id1"] and tickets()[0]["status"] == "CANCELLED"
    assert not execution.cancel(1, now=NOW)["ok"]


def test_status_reports_usage_and_limits(broker):
    add_rec("AAPL")
    execution.propose(now=NOW)
    s = execution.status(NOW)
    assert s["mode"] == "paper" and s["connected"] and s["pending"] == 1 and s["limits"]["max_order_usd"] == 1000.0
    execution.approve(1, now=NOW)
    s = execution.status(NOW)
    assert s["today"] == {"orders": 1, "usd": 1000.0} and s["pending"] == 0


def test_approval_token_check():
    assert "switched off" in execution.check_token("anything", "")
    assert execution.check_token("wrong", "secret") == "Wrong approval token."
    assert execution.check_token(None, "secret") == "Wrong approval token."
    assert execution.check_token("secret", "secret") is None
