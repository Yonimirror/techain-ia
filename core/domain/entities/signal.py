from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from uuid import UUID, uuid4

from core.domain.value_objects import Symbol, Price, Timeframe


class SignalDirection(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    FLAT = "FLAT"  # exit / no position


class SignalStrength(float, Enum):
    WEAK = 0.25
    MODERATE = 0.50
    STRONG = 0.75
    VERY_STRONG = 1.0


@dataclass(frozen=True)
class Signal:
    """Immutable trading signal produced by a strategy."""
    id: UUID
    strategy_id: str
    symbol: Symbol
    direction: SignalDirection
    strength: float                # 0.0 – 1.0
    price: Price                   # reference price at signal time
    timeframe: Timeframe
    timestamp: datetime
    metadata: dict = field(default_factory=dict)  # arbitrary strategy context

    def __post_init__(self) -> None:
        if not (0.0 <= self.strength <= 1.0):
            raise ValueError(f"Signal strength must be in [0, 1], got {self.strength}")

    @classmethod
    def create(
        cls,
        strategy_id: str,
        symbol: Symbol,
        direction: SignalDirection,
        strength: float,
        price: Price,
        timeframe: Timeframe,
        timestamp: datetime | None = None,
        metadata: dict | None = None,
    ) -> "Signal":
        return cls(
            id=uuid4(),
            strategy_id=strategy_id,
            symbol=symbol,
            direction=direction,
            strength=strength,
            price=price,
            timeframe=timeframe,
            timestamp=timestamp or datetime.now(timezone.utc),
            metadata=metadata or {},
        )

    @property
    def is_entry(self) -> bool:
        return self.direction in (SignalDirection.LONG, SignalDirection.SHORT)

    @property
    def is_exit(self) -> bool:
        return self.direction == SignalDirection.FLAT

    def __repr__(self) -> str:
        return (
            f"Signal({self.strategy_id} | {self.symbol} | "
            f"{self.direction.value} | strength={self.strength:.2f})"
        )
