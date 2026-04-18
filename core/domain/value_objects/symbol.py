from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class Symbol:
    ticker: str
    exchange: str = "UNKNOWN"

    def __post_init__(self) -> None:
        if not self.ticker:
            raise ValueError("Symbol ticker cannot be empty")
        object.__setattr__(self, "ticker", self.ticker.upper())

    @classmethod
    def of(cls, ticker: str, exchange: str = "UNKNOWN") -> "Symbol":
        return cls(ticker=ticker, exchange=exchange)

    def __str__(self) -> str:
        return f"{self.ticker}:{self.exchange}"

    def __repr__(self) -> str:
        return f"Symbol({self.ticker!r}, {self.exchange!r})"
