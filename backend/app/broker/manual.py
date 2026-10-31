"""Default broker: does NOT connect anywhere. Orders are never sent; the app just shows recommendations."""
from .base import Broker, OrderRequest, OrderResult, Position


class ManualBroker(Broker):
    name = "manual"
    supports_auto_execution = False

    def place_order(self, order: OrderRequest) -> OrderResult:
        return OrderResult(
            accepted=False,
            message=(f"Manual mode: place this yourself in your broker app -> "
                     f"{order.side.upper()} {order.quantity:g} {order.symbol}"
                     + (f" @ {order.limit_price}" if order.limit_price else "")
                     + (f", stop {order.stop_loss}" if order.stop_loss else "")
                     + (f", target {order.take_profit}" if order.take_profit else "")),
        )

    def get_positions(self) -> list[Position]:
        return []

    def get_cash(self) -> float:
        return 0.0
