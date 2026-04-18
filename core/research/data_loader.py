"""
Data Loader — descarga datos históricos reales via yfinance.

Soporta:
- Crypto: BTC-USD, ETH-USD (Yahoo Finance)
- Acciones: SPY, AAPL, NVDA, cualquier ticker
- Timeframes: 1d, 1h, 4h (via resample desde 1h)

Los datos se cachean en data/historical/ para no repetir descargas.
"""
from __future__ import annotations
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf

from core.domain.entities import MarketData
from core.domain.value_objects import Symbol, Timeframe

logger = logging.getLogger(__name__)

CACHE_DIR = Path("data/historical")

# Mapa de símbolos internos → tickers de Yahoo Finance
YAHOO_TICKERS: dict[str, str] = {
    "BTC": "BTC-USD",
    "ETH": "ETH-USD",
    "SOL": "SOL-USD",
    "SPY": "SPY",
    "AAPL": "AAPL",
    "NVDA": "NVDA",
    "MSFT": "MSFT",
    "QQQ": "QQQ",
    "GLD": "GLD",
    "SMH": "SMH",
    "XLE": "XLE",
    "TLT": "TLT",
}

# Timeframes que Yahoo Finance soporta directamente
YAHOO_INTERVALS: dict[str, str] = {
    "1d": "1d",
    "1h": "1h",
    "4h": "1h",  # descargamos 1h y resampleamos a 4h
}


def load(
    symbol: Symbol,
    timeframe: Timeframe,
    start: datetime | None = None,
    end: datetime | None = None,
    use_cache: bool = True,
) -> MarketData:
    """
    Descarga o carga desde caché datos OHLCV para un símbolo y timeframe.

    Args:
        symbol: Símbolo a cargar (ej: Symbol.of("BTC"))
        timeframe: Timeframe deseado (1d, 4h, 1h)
        start: Fecha inicio. Default: 3 años atrás
        end: Fecha fin. Default: hoy
        use_cache: Si True, usa CSV cacheado si existe y está actualizado

    Returns:
        MarketData con los OHLCV cargados
    """
    if start is None:
        start = datetime.now(timezone.utc) - timedelta(days=365 * 3)
    if end is None:
        end = datetime.now(timezone.utc)

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = CACHE_DIR / f"{symbol.ticker}_{timeframe.value}.csv"

    if use_cache and _cache_valid(cache_path, end):
        logger.info("Loading from cache: %s", cache_path)
        df = pd.read_csv(cache_path, index_col="timestamp")
        df.index = pd.to_datetime(df.index, format="mixed", utc=True)
    else:
        df = _download(symbol, timeframe, start, end)
        if df.empty:
            logger.warning("No data downloaded for %s %s", symbol.ticker, timeframe.value)
            return MarketData(symbol=symbol, timeframe=timeframe, bars=[])
        df.to_csv(cache_path)
        logger.info("Saved to cache: %s (%d bars)", cache_path, len(df))

    # Normalize index to UTC-aware, then filter, then strip tz for domain layer
    if not hasattr(df.index, 'tz') or df.index.tz is None:
        df.index = pd.to_datetime(df.index, format="mixed", utc=True)
    start_utc = pd.Timestamp(start).tz_localize("UTC") if start.tzinfo is None else pd.Timestamp(start).tz_convert("UTC")
    end_utc = pd.Timestamp(end).tz_localize("UTC") if end.tzinfo is None else pd.Timestamp(end).tz_convert("UTC")
    df = df[(df.index >= start_utc) & (df.index <= end_utc)]
    df.index = df.index.tz_localize(None)  # strip tz for domain layer
    return MarketData.from_dataframe(symbol, timeframe, df)


def load_multiple(
    assets: list[tuple[str, str]],  # [(ticker, exchange), ...]
    timeframes: list[Timeframe],
    start: datetime | None = None,
    end: datetime | None = None,
) -> list[MarketData]:
    """
    Carga datos para múltiples activos y timeframes.

    Returns lista de MarketData listos para el Research Engine.
    """
    results = []
    for ticker, exchange in assets:
        symbol = Symbol.of(ticker, exchange)
        for tf in timeframes:
            try:
                md = load(symbol, tf, start, end)
                # Fallback a Alpha Vantage si Yahoo no tiene suficientes datos
                if len(md) < 50:
                    from infrastructure.data_providers.alpha_vantage_provider import (
                        fetch_daily, AV_PREFERRED
                    )
                    if ticker in AV_PREFERRED and tf == Timeframe.D1:
                        logger.info("Yahoo insuficiente para %s — probando Alpha Vantage", ticker)
                        years = max(1, int((end - start).days / 365)) if start and end else 3
                        df_av = fetch_daily(ticker, years=years)
                        if len(df_av) > 50:
                            from core.domain.entities import Bar
                            from decimal import Decimal
                            bars = [
                                Bar(
                                    timestamp=row.Index.to_pydatetime(),
                                    open=Decimal(str(row.open)),
                                    high=Decimal(str(row.high)),
                                    low=Decimal(str(row.low)),
                                    close=Decimal(str(row.close)),
                                    volume=Decimal(str(row.volume)),
                                )
                                for row in df_av.itertuples()
                            ]
                            from core.domain.entities import MarketData as MD
                            md = MD(symbol=symbol, timeframe=tf, bars=bars)
                            logger.info("Alpha Vantage: %d barras para %s %s", len(md), ticker, tf.value)

                if len(md) > 50:
                    results.append(md)
                    logger.info("Loaded %s %s: %d bars", ticker, tf.value, len(md))
                else:
                    logger.warning("Skipping %s %s: only %d bars", ticker, tf.value, len(md))
            except Exception as e:
                logger.error("Failed to load %s %s: %s", ticker, tf.value, e)
    return results


def _download(symbol: Symbol, timeframe: Timeframe, start: datetime, end: datetime) -> pd.DataFrame:
    """Descarga datos desde Yahoo Finance y normaliza columnas."""
    yahoo_ticker = YAHOO_TICKERS.get(symbol.ticker, symbol.ticker)
    yahoo_interval = YAHOO_INTERVALS.get(timeframe.value, "1d")

    # Yahoo Finance limits 1h data to last 730 days
    if yahoo_interval == "1h":
        limit = datetime.now(timezone.utc) - timedelta(days=729)
        if start < limit:
            start = limit
            logger.info("Capping start date to %s for 1h interval", start.date())

    logger.info(
        "Downloading %s (%s) interval=%s from %s to %s",
        symbol.ticker, yahoo_ticker, yahoo_interval,
        start.date(), end.date(),
    )

    ticker = yf.Ticker(yahoo_ticker)
    df = ticker.history(
        start=start.strftime("%Y-%m-%d"),
        end=end.strftime("%Y-%m-%d"),
        interval=yahoo_interval,
        auto_adjust=True,
    )

    if df.empty:
        return pd.DataFrame()

    # Normalizar columnas
    df = df.rename(columns={
        "Open": "open", "High": "high",
        "Low": "low", "Close": "close", "Volume": "volume",
    })
    df = df[["open", "high", "low", "close", "volume"]].dropna()
    df.index.name = "timestamp"

    # Resamplear 1h → 4h si necesario
    if timeframe == Timeframe.H4:
        df = _resample_4h(df)

    return df


def _resample_4h(df: pd.DataFrame) -> pd.DataFrame:
    """Resamplea datos horarios a 4 horas."""
    df_4h = df.resample("4h").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }).dropna()
    return df_4h


def _cache_valid(path: Path, end: datetime) -> bool:
    """Cache válido si existe y fue actualizado hoy (o el end es pasado)."""
    if not path.exists():
        return False
    mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    end_is_past = end.date() < datetime.now(timezone.utc).date()
    if end_is_past:
        return True  # datos históricos no cambian
    return (datetime.now(timezone.utc) - mtime).total_seconds() < 3600  # refresco cada hora
