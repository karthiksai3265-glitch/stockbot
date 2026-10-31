"""Broker registry. Add a new broker by implementing `Broker` and registering it here."""
from .. import config
from .alpaca import AlpacaBroker
from .base import Broker, OrderRequest, OrderResult, Position
from .manual import ManualBroker

REGISTRY: dict[str, type[Broker]] = {
    "none": ManualBroker,
    "manual": ManualBroker,
    "alpaca": AlpacaBroker,
}


def get_broker() -> Broker:
    try:
        return REGISTRY[config.BROKER]()
    except KeyError:
        raise ValueError(f"Unknown broker '{config.BROKER}'. Available: {sorted(REGISTRY)}")


__all__ = ["Broker", "OrderRequest", "OrderResult", "Position", "ManualBroker", "AlpacaBroker", "get_broker"]
