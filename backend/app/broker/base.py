"""Broker interface. The rest of the app only talks to this abstract class.

Today the app runs with ManualBroker (no connection: you place trades yourself in your broker app).
To add real execution later, implement a subclass (e.g. AlpacaBroker) and register it in broker/__init__.py.
Recommended rollout: paper trading -> one-tap approval -> (optionally) auto mode with hard limits.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Literal

Side = Literal["buy", "sell"]


@dataclass
class OrderRequest:
    symbol: str
    side: Side
    quantity: float
    order_type: Literal["market", "limit"] = "limit"
    limit_price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    recommendation_id: int | None = None  # links the order back to the logged recommendation


@dataclass
class OrderResult:
    accepted: bool
    order_id: str | None = None
    message: str = ""
    raw: dict = field(default_factory=dict)


@dataclass
class Position:
    symbol: str
    quantity: float
    avg_price: float


class Broker(ABC):
    name: str = "abstract"
    mode: str = "manual"          # "manual", "paper" or "live"
    # Safety: real brokers must set this True only when the user explicitly enables auto-execution.
    supports_auto_execution: bool = False

    def supports(self, symbol: str) -> bool:
        """Whether this broker can trade the symbol (e.g. Alpaca trades US stocks only)."""
        return True

    def get_equity(self) -> float:
        return self.get_cash()

    @abstractmethod
    def place_order(self, order: OrderRequest) -> OrderResult: ...

    @abstractmethod
    def get_positions(self) -> list[Position]: ...

    @abstractmethod
    def get_cash(self) -> float: ...

    def cancel_order(self, order_id: str) -> bool:
        return False
