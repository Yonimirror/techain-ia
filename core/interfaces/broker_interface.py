from __future__ import annotations
from abc import ABC, abstractmethod
from decimal import Decimal

from core.domain.entities import Order
from core.domain.value_objects import Symbol, Price, Quantity


class IBroker(ABC):
    """
    Low-level broker connectivity contract.

    The ExecutionEngine uses this. Strategies and the risk engine
    NEVER interact with this layer directly.
    """

    @abstractmethod
    async def submit_order(self, order: Order) -> str:
        """Submit order to broker. Returns broker order ID."""
        ...

    @abstractmethod
    async def cancel_order(self, broker_order_id: str) -> bool:
        """Cancel order by broker ID. Returns True if successful."""
        ...

    @abstractmethod
    async def get_account_balance(self) -> Decimal:
        """Return available cash balance."""
        ...

    @abstractmethod
    async def get_positions(self) -> dict[str, Quantity]:
        """Return current open positions: symbol_str -> quantity."""
        ...

    @abstractmethod
    async def is_connected(self) -> bool:
        """Check broker connectivity."""
        ...
