from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal

from core.domain.entities import Order
from core.domain.value_objects import Price, Quantity


@dataclass
class OrderResult:
    """Result returned after order submission."""
    order: Order
    success: bool
    filled_price: Price | None = None
    filled_quantity: Quantity | None = None
    broker_order_id: str | None = None
    fees: Decimal = Decimal("0")
    error_message: str | None = None

    @property
    def failed(self) -> bool:
        return not self.success


class IExecutionEngine(ABC):
    """
    Contract for order execution.

    Abstracts the broker layer. Handles slippage simulation in backtesting
    and real submission in live trading.
    """

    @abstractmethod
    async def execute(self, order: Order) -> OrderResult:
        """
        Submit an order and return the result.

        Args:
            order: The Order entity to execute.

        Returns:
            OrderResult with fill details or error information.
        """
        ...

    @abstractmethod
    async def cancel(self, order: Order) -> bool:
        """Cancel a pending/submitted order. Returns True if cancelled."""
        ...

    @abstractmethod
    async def get_order_status(self, order: Order) -> Order:
        """Refresh and return updated order status."""
        ...
