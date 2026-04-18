from __future__ import annotations
from abc import ABC, abstractmethod
from datetime import datetime

from core.domain.entities import MarketData
from core.domain.value_objects import Symbol, Timeframe, Price


class IDataProvider(ABC):
    """
    Contract for market data providers.

    Implementations: CSV files, REST APIs, WebSocket feeds, databases.
    """

    @abstractmethod
    async def get_historical(
        self,
        symbol: Symbol,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> MarketData:
        """Fetch historical OHLCV data for a symbol."""
        ...

    @abstractmethod
    async def get_latest_bars(
        self,
        symbol: Symbol,
        timeframe: Timeframe,
        count: int = 100,
    ) -> MarketData:
        """Fetch the most recent N bars."""
        ...

    @abstractmethod
    async def get_current_price(self, symbol: Symbol) -> Price:
        """Fetch current bid/ask midpoint."""
        ...

    @abstractmethod
    async def subscribe(self, symbol: Symbol, timeframe: Timeframe) -> None:
        """Subscribe to live bar updates (streaming providers)."""
        ...

    @abstractmethod
    async def unsubscribe(self, symbol: Symbol, timeframe: Timeframe) -> None:
        """Unsubscribe from live bar updates."""
        ...
