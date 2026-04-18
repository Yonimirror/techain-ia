from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from core.domain.value_objects import Symbol, Price, Quantity
from core.domain.entities.order import OrderSide


@dataclass
class Position:
    """Tracks an open position for a symbol."""
    symbol: Symbol
    side: OrderSide
    quantity: Quantity
    average_entry_price: Price
    opened_at: datetime
    trade_ids: list[UUID] = field(default_factory=list)
    last_updated: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def notional_value(self) -> Decimal:
        return self.average_entry_price.value * self.quantity.value

    def unrealized_pnl(self, current_price: Price) -> Decimal:
        qty = self.quantity.value
        if self.side == OrderSide.BUY:
            return (current_price.value - self.average_entry_price.value) * qty
        else:
            return (self.average_entry_price.value - current_price.value) * qty

    def unrealized_pnl_pct(self, current_price: Price) -> Decimal:
        cost = self.notional_value
        if cost == 0:
            return Decimal("0")
        return self.unrealized_pnl(current_price) / cost * 100

    def add_quantity(self, quantity: Quantity, price: Price) -> None:
        """Average-in to existing position."""
        total_cost = self.average_entry_price.value * self.quantity.value
        new_cost = price.value * quantity.value
        new_qty = self.quantity.value + quantity.value
        self.average_entry_price = Price(
            (total_cost + new_cost) / new_qty
        )
        self.quantity = Quantity(new_qty)
        self.last_updated = datetime.now(timezone.utc)

    def reduce_quantity(self, quantity: Quantity) -> None:
        if quantity > self.quantity:
            raise ValueError("Cannot reduce more than current position quantity")
        self.quantity = Quantity(self.quantity.value - quantity.value)
        self.last_updated = datetime.now(timezone.utc)

    @property
    def is_flat(self) -> bool:
        return self.quantity.value == Decimal("0")

    def __repr__(self) -> str:
        return (
            f"Position({self.symbol} | {self.side.value} | "
            f"qty={self.quantity} | avg={self.average_entry_price})"
        )
