from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from uuid import UUID, uuid4

from core.domain.value_objects import Symbol, Price, Quantity


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"
    STOP_LIMIT = "STOP_LIMIT"


class OrderStatus(str, Enum):
    PENDING = "PENDING"
    SUBMITTED = "SUBMITTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


@dataclass
class Order:
    """Mutable order lifecycle entity."""
    id: UUID
    symbol: Symbol
    side: OrderSide
    order_type: OrderType
    quantity: Quantity
    status: OrderStatus
    created_at: datetime
    limit_price: Price | None = None
    stop_price: Price | None = None
    filled_price: Price | None = None
    filled_quantity: Quantity | None = None
    filled_at: datetime | None = None
    broker_order_id: str | None = None
    strategy_id: str | None = None
    signal_id: UUID | None = None
    metadata: dict = field(default_factory=dict)

    @classmethod
    def create_market(
        cls,
        symbol: Symbol,
        side: OrderSide,
        quantity: Quantity,
        strategy_id: str | None = None,
        signal_id: UUID | None = None,
    ) -> "Order":
        return cls(
            id=uuid4(),
            symbol=symbol,
            side=side,
            order_type=OrderType.MARKET,
            quantity=quantity,
            status=OrderStatus.PENDING,
            created_at=datetime.now(timezone.utc),
            strategy_id=strategy_id,
            signal_id=signal_id,
        )

    @classmethod
    def create_limit(
        cls,
        symbol: Symbol,
        side: OrderSide,
        quantity: Quantity,
        limit_price: Price,
        strategy_id: str | None = None,
        signal_id: UUID | None = None,
    ) -> "Order":
        return cls(
            id=uuid4(),
            symbol=symbol,
            side=side,
            order_type=OrderType.LIMIT,
            quantity=quantity,
            limit_price=limit_price,
            status=OrderStatus.PENDING,
            created_at=datetime.now(timezone.utc),
            strategy_id=strategy_id,
            signal_id=signal_id,
        )

    @property
    def is_filled(self) -> bool:
        return self.status == OrderStatus.FILLED

    @property
    def is_active(self) -> bool:
        return self.status in (OrderStatus.PENDING, OrderStatus.SUBMITTED, OrderStatus.PARTIALLY_FILLED)

    def fill(self, price: Price, quantity: Quantity, filled_at: datetime | None = None) -> None:
        self.filled_price = price
        self.filled_quantity = quantity
        self.filled_at = filled_at or datetime.now(timezone.utc)
        self.status = OrderStatus.FILLED

    def reject(self, reason: str = "") -> None:
        self.status = OrderStatus.REJECTED
        self.metadata["rejection_reason"] = reason

    def __repr__(self) -> str:
        return (
            f"Order({self.id} | {self.symbol} | {self.side.value} "
            f"{self.quantity} @ {self.order_type.value} | {self.status.value})"
        )
