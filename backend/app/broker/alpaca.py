"""Alpaca broker (US stocks). PAPER trading by default.

Endpoints, headers and the bracket-order fields were checked against Alpaca's documentation, but this code has
not been run against the live service from the build environment. Test it on a paper account first.

- Paper (default): https://paper-api.alpaca.markets   Live: https://api.alpaca.markets (needs STOCKBOT_ALPACA_LIVE=yes)
- BUY  -> one limit order for whole shares with an attached take-profit and stop-loss (order_class "bracket").
          time_in_force is "gtc" so the stop and target legs stay active after the entry fills. The entry
          order also stays open until it fills or you cancel it, so cancel stale ones.
- SELL -> closes the whole position at market (only if you hold it).
"""
from __future__ import annotations

import logging
import re

import requests

from .. import config
from .base import Broker, OrderRequest, OrderResult, Position

log = logging.getLogger(__name__)
PAPER_URL = "https://paper-api.alpaca.markets"
LIVE_URL = "https://api.alpaca.markets"


def to_alpaca_symbol(symbol: str) -> str:
    return symbol.replace("-", ".")           # Yahoo "BRK-B" -> Alpaca "BRK.B"


class AlpacaBroker(Broker):
    name = "alpaca"
    supports_auto_execution = False            # every order needs an explicit approval (see execution.py)

    def __init__(self, session: requests.Session | None = None):
        if not (config.ALPACA_KEY and config.ALPACA_SECRET):
            raise ValueError("Set ALPACA_API_KEY and ALPACA_API_SECRET to use the Alpaca broker.")
        self.mode = "live" if config.ALPACA_LIVE else "paper"
        self.base = LIVE_URL if config.ALPACA_LIVE else PAPER_URL
        self.session = session or requests.Session()
        self.session.headers.update({"APCA-API-KEY-ID": config.ALPACA_KEY, "APCA-API-SECRET-KEY": config.ALPACA_SECRET})

    # ---- helpers
    def supports(self, symbol: str) -> bool:
        if symbol.endswith((".AX", ".NS")):
            return False
        return re.fullmatch(r"[A-Z]{1,5}(\.[A-Z])?", to_alpaca_symbol(symbol)) is not None

    def _call(self, method: str, path: str, **kw):
        r = self.session.request(method, self.base + path, timeout=15, **kw)
        if not r.ok:
            raise RuntimeError(f"Alpaca {r.status_code}: {r.text[:300]}")
        return r.json() if r.text else {}

    # ---- Broker interface
    def get_cash(self) -> float:
        return float(self._call("GET", "/v2/account")["cash"])

    def get_equity(self) -> float:
        return float(self._call("GET", "/v2/account")["equity"])

    def get_positions(self) -> list[Position]:
        return [Position(p["symbol"], float(p["qty"]), float(p["avg_entry_price"])) for p in self._call("GET", "/v2/positions")]

    def place_order(self, order: OrderRequest) -> OrderResult:
        sym = to_alpaca_symbol(order.symbol)
        if not self.supports(order.symbol):
            return OrderResult(False, message=f"Alpaca trades US stocks only; {order.symbol} is not supported")
        try:
            if order.side == "sell":
                held = {p.symbol: p for p in self.get_positions()}
                if sym not in held:
                    return OrderResult(False, message=f"You do not hold {sym}, so there is nothing to sell")
                resp = self._call("DELETE", f"/v2/positions/{sym}")
                return OrderResult(True, order_id=str(resp.get("id", "")), message=f"Closing position in {sym} at market ({self.mode})", raw=resp)

            qty = int(order.quantity)
            if qty < 1 or qty != order.quantity:
                return OrderResult(False, message="Quantity must be a whole number of shares, at least 1")
            if not (order.limit_price and order.stop_loss and order.take_profit):
                return OrderResult(False, message="A buy needs a limit price, a stop-loss and a take-profit")
            if not (order.stop_loss < order.limit_price < order.take_profit):
                return OrderResult(False, message="Prices must satisfy stop-loss < entry < target for a buy")
            body = {
                "symbol": sym, "qty": str(qty), "side": "buy", "type": "limit", "time_in_force": "gtc",
                "limit_price": f"{order.limit_price:.2f}", "order_class": "bracket",
                "take_profit": {"limit_price": f"{order.take_profit:.2f}"},
                "stop_loss": {"stop_price": f"{order.stop_loss:.2f}"},
            }
            resp = self._call("POST", "/v2/orders", json=body)
            return OrderResult(True, order_id=resp.get("id"), message=f"Submitted to Alpaca ({self.mode}): buy {qty} {sym} limit {order.limit_price:.2f}", raw=resp)
        except Exception as e:     # network problems, rejected orders, bad keys
            log.warning("Alpaca order failed: %s", e)
            return OrderResult(False, message=str(e))

    def cancel_order(self, order_id: str) -> bool:
        try:
            self._call("DELETE", f"/v2/orders/{order_id}")
            return True
        except Exception as e:
            log.warning("Alpaca cancel failed: %s", e)
            return False
