"""
CSV data provider — loads historical OHLCV data from CSV files.

Expected CSV format:
    timestamp,open,high,low,close,volume

Timestamps: ISO 8601 format.
"""
from __future__ import annotations
import logging
from datetime import datetime
from pathlib import Path

import pandas as pd

from core.domain.entities import MarketData
from core.domain.value_objects import Symbol, Timeframe, Price
from core.interfaces.data_provider_interface import IDataProvider

logger = logging.getLogger(__name__)


class CSVDataProvider(IDataProvider):

    def __init__(self, data_dir: str = "data/historical") -> None:
        self._data_dir = Path(data_dir)

    def _csv_path(self, symbol: Symbol, timeframe: Timeframe) -> Path:
        return self._data_dir / f"{symbol.ticker}_{timeframe.value}.csv"

    async def get_historical(
        self,
        symbol: Symbol,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> MarketData:
        df = self._load(symbol, timeframe)
        mask = (df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end))
        return MarketData.from_dataframe(symbol, timeframe, df[mask])

    async def get_latest_bars(
        self,
        symbol: Symbol,
        timeframe: Timeframe,
        count: int = 100,
    ) -> MarketData:
        df = self._load(symbol, timeframe)
        return MarketData.from_dataframe(symbol, timeframe, df.tail(count))

    async def get_current_price(self, symbol: Symbol) -> Price:
        # For CSV provider, return latest close
        df = self._load(symbol, Timeframe.D1)
        if df.empty:
            raise ValueError(f"No data for {symbol}")
        return Price.of(df["close"].iloc[-1])

    async def subscribe(self, symbol: Symbol, timeframe: Timeframe) -> None:
        logger.debug("CSV provider: subscribe is a no-op for %s %s", symbol, timeframe)

    async def unsubscribe(self, symbol: Symbol, timeframe: Timeframe) -> None:
        pass

    def _load(self, symbol: Symbol, timeframe: Timeframe) -> pd.DataFrame:
        path = self._csv_path(symbol, timeframe)
        if not path.exists():
            logger.warning("CSV file not found: %s", path)
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        df = pd.read_csv(path, index_col="timestamp")
        df.index = pd.to_datetime(df.index, format="mixed", utc=True).tz_convert(None)
        df = df.sort_index()
        return df
