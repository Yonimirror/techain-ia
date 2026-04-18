"""
Binance data provider — fetches live OHLCV data from Binance REST API.

Reads credentials from environment variables:
    BINANCE_API_KEY
    BINANCE_SECRET_KEY

Only uses read-only endpoints. No trading permissions required.

Usage:
    provider = BinanceDataProvider()
    data = await provider.get_latest_bars(Symbol.of("BTC", "CRYPTO"), Timeframe("4h"), count=500)
"""
from __future__ import annotations
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from core.domain.entities import MarketData, OHLCV
from core.domain.value_objects import Symbol, Timeframe, Price, Quantity
from core.interfaces.data_provider_interface import IDataProvider

logger = logging.getLogger(__name__)

# Binance timeframe mapping
_TF_MAP = {
    "1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m",
    "1h": "1h", "4h": "4h", "1d": "1d", "1w": "1w",
}

# Binance symbol mapping (ticker → USDT pair)
_SYMBOL_MAP = {
    "BTC": "BTCUSDT",
    "ETH": "ETHUSDT",
    "BNB": "BNBUSDT",
    "SOL": "SOLUSDT",
}


class BinanceDataProvider(IDataProvider):
    """
    Fetches OHLCV data from Binance REST API.

    Falls back to CSV cache if Binance is unreachable.
    Always saves fetched data to CSV cache for offline use.
    """

    def __init__(self, data_dir: str = "data/historical") -> None:
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._client = self._build_client()

    def _build_client(self):
        try:
            from binance.client import Client
            api_key = os.environ.get("BINANCE_API_KEY", "")
            api_secret = os.environ.get("BINANCE_SECRET_KEY", "")
            if not api_key or not api_secret:
                logger.warning("Binance credentials not found in environment — read-only mode with empty keys")
            client = Client(api_key, api_secret)
            logger.info("Binance client initialized")
            return client
        except Exception as e:
            logger.error("Failed to initialize Binance client: %s", e)
            return None

    def _binance_symbol(self, symbol: Symbol) -> str:
        return _SYMBOL_MAP.get(symbol.ticker, f"{symbol.ticker}USDT")

    def _binance_interval(self, timeframe: Timeframe) -> str:
        tf = _TF_MAP.get(timeframe.value)
        if tf is None:
            raise ValueError(f"Unsupported timeframe for Binance: {timeframe.value}")
        return tf

    async def get_latest_bars(
        self,
        symbol: Symbol,
        timeframe: Timeframe,
        count: int = 500,
    ) -> MarketData:
        if self._client is None:
            return await self._fallback_csv(symbol, timeframe, count)

        try:
            binance_sym = self._binance_symbol(symbol)
            interval = self._binance_interval(timeframe)
            logger.info("Fetching %d bars from Binance: %s %s", count, binance_sym, interval)

            klines = self._client.get_klines(
                symbol=binance_sym,
                interval=interval,
                limit=count,
            )

            bars = self._parse_klines(klines, symbol, timeframe)
            md = MarketData(symbol=symbol, timeframe=timeframe, bars=bars)

            # Save to CSV cache for offline/research use
            self._save_to_csv(md, symbol, timeframe)
            logger.info("Fetched and cached %d bars for %s %s", len(bars), symbol.ticker, timeframe.value)
            return md

        except Exception as e:
            logger.warning("Binance fetch failed (%s) — falling back to CSV", e)
            return await self._fallback_csv(symbol, timeframe, count)

    async def get_historical(
        self,
        symbol: Symbol,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> MarketData:
        if self._client is None:
            return await self._fallback_csv(symbol, timeframe, 1000)

        try:
            binance_sym = self._binance_symbol(symbol)
            interval = self._binance_interval(timeframe)

            start_ms = int(start.timestamp() * 1000)
            end_ms = int(end.timestamp() * 1000)

            klines = self._client.get_historical_klines(
                symbol=binance_sym,
                interval=interval,
                start_str=start_ms,
                end_str=end_ms,
            )

            bars = self._parse_klines(klines, symbol, timeframe)
            md = MarketData(symbol=symbol, timeframe=timeframe, bars=bars)
            self._save_to_csv(md, symbol, timeframe)
            return md

        except Exception as e:
            logger.warning("Binance historical fetch failed (%s) — falling back to CSV", e)
            return await self._fallback_csv(symbol, timeframe, 1000)

    async def get_current_price(self, symbol: Symbol) -> Price:
        if self._client is None:
            raise ValueError("Binance client not available")
        try:
            binance_sym = self._binance_symbol(symbol)
            ticker = self._client.get_symbol_ticker(symbol=binance_sym)
            return Price.of(float(ticker["price"]))
        except Exception as e:
            logger.error("Failed to get current price for %s: %s", symbol.ticker, e)
            raise

    async def subscribe(self, symbol: Symbol, timeframe: Timeframe) -> None:
        logger.debug("Binance provider: streaming subscribe not implemented yet for %s %s", symbol, timeframe)

    async def unsubscribe(self, symbol: Symbol, timeframe: Timeframe) -> None:
        pass

    def _parse_klines(self, klines: list, symbol: Symbol, timeframe: Timeframe) -> list[OHLCV]:
        bars = []
        for k in klines:
            # Binance kline format: [open_time, open, high, low, close, volume, ...]
            ts = datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc).replace(tzinfo=None)
            bars.append(OHLCV(
                timestamp=ts,
                open=Price.of(float(k[1])),
                high=Price.of(float(k[2])),
                low=Price.of(float(k[3])),
                close=Price.of(float(k[4])),
                volume=Quantity.of(float(k[5])),
            ))
        return bars

    def _csv_path(self, symbol: Symbol, timeframe: Timeframe) -> Path:
        return self._data_dir / f"{symbol.ticker}_{timeframe.value}.csv"

    def _save_to_csv(self, md: MarketData, symbol: Symbol, timeframe: Timeframe) -> None:
        """Merge new bars into existing CSV cache."""
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
        """Load from CSV cache when Binance is unavailable."""
        import pandas as pd

        path = self._csv_path(symbol, timeframe)
        if not path.exists():
            logger.error("No CSV cache for %s %s and Binance unavailable", symbol.ticker, timeframe.value)
            return MarketData(symbol=symbol, timeframe=timeframe, bars=[])

        df = pd.read_csv(path, index_col="timestamp")
        df.index = pd.to_datetime(df.index, format="mixed", utc=True).tz_convert(None)
        df = df.sort_index().tail(count)
        logger.info("Loaded %d bars from CSV cache for %s %s", len(df), symbol.ticker, timeframe.value)
        return MarketData.from_dataframe(symbol, timeframe, df)
