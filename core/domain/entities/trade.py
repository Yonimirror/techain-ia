from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from uuid import UUID, uuid4

from core.domain.value_objects import Symbol, Price, Quantity
from core.domain.entities.order import OrderSide


class TradeStatus(str, Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"


@dataclass
class Trade:
    """Represents a complete round-trip trade (entry + optional exit)."""
    id: UUID
    symbol: Symbol
    side: OrderSide
    entry_price: Price
    quantity: Quantity
    entry_order_id: UUID
    strategy_id: str
    status: TradeStatus
    opened_at: datetime
    exit_price: Price | None = None
    exit_order_id: UUID | None = None
    closed_at: datetime | None = None
    fees: Decimal = Decimal("0")
    metadata: dict = field(default_factory=dict)

    @classmethod
    def open(
        cls,
        symbol: Symbol,
        side: OrderSide,
        entry_price: Price,
        quantity: Quantity,
        entry_order_id: UUID,
        strategy_id: str,
        opened_at: datetime | None = None,
        fees: Decimal = Decimal("0"),
    ) -> "Trade":
        return cls(
            id=uuid4(),
            symbol=symbol,
            side=side,
            entry_price=entry_price,
            quantity=quantity,
            entry_order_id=entry_order_id,
            strategy_id=strategy_id,
            status=TradeStatus.OPEN,
            opened_at=opened_at or datetime.now(timezone.utc),
            fees=fees,
        )

    def close(
        self,
        exit_price: Price,
        exit_order_id: UUID,
        closed_at: datetime | None = None,
        exit_fees: Decimal = Decimal("0"),
    ) -> None:
        self.exit_price = exit_price
        self.exit_order_id = exit_order_id
        self.closed_at = closed_at or datetime.now(timezone.utc)
        self.fees += exit_fees
        self.status = TradeStatus.CLOSED

    @property
    def pnl(self) -> Decimal:
        if self.exit_price is None:
            return Decimal("0")
        qty = self.quantity.value
        if self.side == OrderSide.BUY:
            gross = (self.exit_price.value - self.entry_price.value) * qty
        else:
            gross = (self.entry_price.value - self.exit_price.value) * qty
        return gross - self.fees

    @property
    def pnl_pct(self) -> Decimal:
        if self.exit_price is None:
            return Decimal("0")
        cost = self.entry_price.value * self.quantity.value
        return (self.pnl / cost * 100) if cost else Decimal("0")

    @property
    def duration_seconds(self) -> float | None:
        if self.closed_at is None:
            return None
        return (self.closed_at - self.opened_at).total_seconds()

    def __repr__(self) -> str:
        return (
            f"Trade({self.symbol} | {self.side.value} | "
            f"pnl={self.pnl:.4f} | {self.status.value})"
        )
