from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

import pandas as pd

from core.domain.value_objects import Symbol, Price, Quantity, Timeframe


@dataclass(frozen=True)
class OHLCV:
    """Single candlestick bar."""
    timestamp: datetime
    open: Price
    high: Price
    low: Price
    close: Price
    volume: Quantity

    def __post_init__(self) -> None:
        if self.high < self.low:
            raise ValueError(f"High {self.high} < Low {self.low}")
        if self.open < Price.of(0) or self.close < Price.of(0):
            raise ValueError("Open/Close prices cannot be negative")

    @property
    def body_size(self) -> Decimal:
        return abs(self.close.value - self.open.value)

    @property
    def is_bullish(self) -> bool:
        return self.close.value > self.open.value

    @property
    def is_bearish(self) -> bool:
        return self.close.value < self.open.value

    @classmethod
    def from_dict(cls, d: dict) -> "OHLCV":
        return cls(
            timestamp=d["timestamp"],
            open=Price.of(d["open"]),
            high=Price.of(d["high"]),
            low=Price.of(d["low"]),
            close=Price.of(d["close"]),
            volume=Quantity.of(d["volume"]),
        )


@dataclass
class MarketData:
    """Collection of OHLCV bars for a symbol/timeframe."""
    symbol: Symbol
    timeframe: Timeframe
    bars: list[OHLCV] = field(default_factory=list)

    @property
    def latest(self) -> OHLCV | None:
        return self.bars[-1] if self.bars else None

    @property
    def closes(self) -> list[Decimal]:
        return [bar.close.value for bar in self.bars]

    @property
    def volumes(self) -> list[Decimal]:
        return [bar.volume.value for bar in self.bars]

    def to_dataframe(self) -> pd.DataFrame:
        return pd.DataFrame([
            {
                "timestamp": b.timestamp,
                "open": float(b.open.value),
                "high": float(b.high.value),
                "low": float(b.low.value),
                "close": float(b.close.value),
                "volume": float(b.volume.value),
            }
            for b in self.bars
        ]).set_index("timestamp")

    @classmethod
    def from_dataframe(cls, symbol: Symbol, timeframe: Timeframe, df: pd.DataFrame) -> "MarketData":
        bars = [
            OHLCV.from_dict({
                "timestamp": idx,
                "open": row["open"],
                "high": row["high"],
                "low": row["low"],
                "close": row["close"],
                "volume": row["volume"],
            })
            for idx, row in df.iterrows()
        ]
        return cls(symbol=symbol, timeframe=timeframe, bars=bars)

    def __len__(self) -> int:
        return len(self.bars)

    def __repr__(self) -> str:
        return f"MarketData({self.symbol} | {self.timeframe.value} | {len(self.bars)} bars)"
