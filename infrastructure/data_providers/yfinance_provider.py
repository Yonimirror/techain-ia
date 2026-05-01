"""
yFinance data provider — fetches OHLCV data for equities, ETFs and futures.

Used for non-crypto symbols (NYSE/NASDAQ/ETFs) that are not available on Binance.
Falls back to CSV cache when yfinance is unavailable.

Usage:
    provider = YFinanceDataProvider()
    data = await provider.get_latest_bars(Symbol.of("NVDA", "NYSE"), Timeframe("1d"), count=500)
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

from core.domain.entities import MarketData, OHLCV
from core.domain.value_objects import Symbol, Timeframe, Price, Quantity
from core.interfaces.data_provider_interface import IDataProvider

logger = logging.getLogger(__name__)

# yfinance period/interval mapping
_INTERVAL_MAP = {
    "1m": "1m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1h": "1h",
    "4h": "1h",   # yfinance has no 4h — fetch 1h and resample
    "1d": "1d",
    "1w": "1wk",
}

# How many calendar days to request for each timeframe to get `count` bars
_DAYS_FOR_COUNT = {
    "1m": 1,
    "5m": 3,
    "15m": 7,
    "30m": 14,
    "1h": 30,
    "4h": 120,
    "1d": 730,
    "1w": 3650,
}


class YFinanceDataProvider(IDataProvider):
    """
    Fetches OHLCV data via yfinance for equities, ETFs, and futures.

    Automatically resamples 1h → 4h when timeframe is "4h".
    Saves to CSV cache for offline use and faster subsequent loads.
    """

    def __init__(self, data_dir: str = "data/historical") -> None:
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)

    async def get_latest_bars(
        self,
        symbol: Symbol,
        timeframe: Timeframe,
        count: int = 500,
    ) -> MarketData:
        try:
            import yfinance as yf
            import pandas as pd

            tf_str = timeframe.value
            interval = _INTERVAL_MAP.get(tf_str, "1d")
            days = _DAYS_FOR_COUNT.get(tf_str, 730)
            # Request extra days to guarantee `count` bars after weekends/holidays
            period_days = max(days, count * 2)

            logger.info("Fetching %s %s via yfinance (period=%dd)", symbol.ticker, tf_str, period_days)

            df = yf.download(
                symbol.ticker,
                period=f"{period_days}d",
                interval=interval,
                progress=False,
                auto_adjust=True,
            )

            if df is None or df.empty:
                logger.warning("yfinance returned empty data for %s %s", symbol.ticker, tf_str)
                return await self._fallback_csv(symbol, timeframe, count)

            # Flatten MultiIndex columns if present (yfinance ≥0.2 returns MultiIndex)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)

            df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
            df.columns = ["open", "high", "low", "close", "volume"]

            # Resample 1h → 4h
            if tf_str == "4h":
                df = self._resample_to_4h(df)

            df = df.dropna().sort_index().tail(count)

            bars = self._df_to_bars(df, symbol, timeframe)
            md = MarketData(symbol=symbol, timeframe=timeframe, bars=bars)
            self._save_to_csv(md, symbol, timeframe)
            logger.info("Fetched %d bars for %s %s via yfinance", len(bars), symbol.ticker, tf_str)
            return md

        except Exception as e:
            logger.warning("yfinance fetch failed for %s %s: %s — falling back to CSV", symbol.ticker, timeframe.value, e)
            return await self._fallback_csv(symbol, timeframe, count)

    async def get_historical(
        self,
        symbol: Symbol,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> MarketData:
        try:
            import yfinance as yf
            import pandas as pd

            tf_str = timeframe.value
            interval = _INTERVAL_MAP.get(tf_str, "1d")

            df = yf.download(
                symbol.ticker,
                start=start.strftime("%Y-%m-%d"),
                end=end.strftime("%Y-%m-%d"),
                interval=interval,
                progress=False,
                auto_adjust=True,
            )

            if df is None or df.empty:
                return await self._fallback_csv(symbol, timeframe, 1000)

            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)

            df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
            df.columns = ["open", "high", "low", "close", "volume"]

            if tf_str == "4h":
                df = self._resample_to_4h(df)

            df = df.dropna().sort_index()
            bars = self._df_to_bars(df, symbol, timeframe)
            md = MarketData(symbol=symbol, timeframe=timeframe, bars=bars)
            self._save_to_csv(md, symbol, timeframe)
            return md

        except Exception as e:
            logger.warning("yfinance historical failed for %s: %s", symbol.ticker, e)
            return await self._fallback_csv(symbol, timeframe, 1000)

    async def get_current_price(self, symbol: Symbol) -> Price:
        try:
            import yfinance as yf
            ticker = yf.Ticker(symbol.ticker)
            info = ticker.fast_info
            price = float(info.last_price)
            return Price.of(price)
        except Exception as e:
            logger.error("Failed to get current price for %s: %s", symbol.ticker, e)
            raise

    async def subscribe(self, symbol: Symbol, timeframe: Timeframe) -> None:
        pass

    async def unsubscribe(self, symbol: Symbol, timeframe: Timeframe) -> None:
        pass

    # ------------------------------------------------------------------ #
    # Internal helpers                                                     #
    # ------------------------------------------------------------------ #

    def _resample_to_4h(self, df):
        """Resample 1h OHLCV DataFrame to 4h bars."""
        return df.resample("4h").agg({
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        }).dropna()

    def _df_to_bars(self, df, symbol: Symbol, timeframe: Timeframe) -> list[OHLCV]:
        bars = []
        for ts, row in df.iterrows():
            # Strip timezone info for consistency with BinanceDataProvider
            if hasattr(ts, "tzinfo") and ts.tzinfo is not None:
                ts = ts.tz_convert("UTC").replace(tzinfo=None)
            bars.append(OHLCV(
                timestamp=ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts,
                open=Price.of(float(row["open"])),
                high=Price.of(float(row["high"])),
                low=Price.of(float(row["low"])),
                close=Price.of(float(row["close"])),
                volume=Quantity.of(float(row["volume"])),
            ))
        return bars

    def _csv_path(self, symbol: Symbol, timeframe: Timeframe) -> Path:
        return self._data_dir / f"{symbol.ticker}_{timeframe.value}.csv"

    def _save_to_csv(self, md: MarketData, symbol: Symbol, timeframe: Timeframe) -> None:
        import pandas as pd
        path = self._csv_path(symbol, timeframe)
        new_rows = [
            {
                "timestamp": b.timestamp.isoformat(),
                "open": float(b.open.value),
                "high": float(b.high.value),
                "low": float(b.low.value),
                "close": float(b.close.value),
                "volume": float(b.volume.value),
            }
            for b in md.bars
        ]
        new_df = pd.DataFrame(new_rows).set_index("timestamp")
        if path.exists():
            old_df = pd.read_csv(path, index_col="timestamp")
            combined = pd.concat([old_df, new_df])
            combined = combined[~combined.index.duplicated(keep="last")]
            combined.sort_index(inplace=True)
        else:
            combined = new_df
        combined.to_csv(path)
        logger.debug("CSV cache updated: %s (%d rows)", path, len(combined))

    async def _fallback_csv(self, symbol: Symbol, timeframe: Timeframe, count: int) -> MarketData:
        import pandas as pd
        path = self._csv_path(symbol, timeframe)
        if not path.exists():
            logger.error("No CSV cache for %s %s and yfinance unavailable", symbol.ticker, timeframe.value)
            return MarketData(symbol=symbol, timeframe=timeframe, bars=[])
        df = pd.read_csv(path, index_col="timestamp")
        df.index = pd.to_datetime(df.index, format="mixed", utc=True).tz_convert(None)
        df = df.sort_index().tail(count)
        logger.info("Loaded %d bars from CSV cache for %s %s", len(df), symbol.ticker, timeframe.value)
        return MarketData.from_dataframe(symbol, timeframe, df)
