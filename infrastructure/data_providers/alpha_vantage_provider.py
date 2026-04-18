"""
Alpha Vantage data provider — fallback para acciones, forex y futuros
donde Yahoo Finance tiene cobertura limitada.

Requiere ALPHA_VANTAGE_API_KEY en .env
Límite gratuito: 500 calls/día, 25 calls/hora
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

logger = logging.getLogger(__name__)

AV_BASE = "https://www.alphavantage.co/query"

# Mapeo de timeframes del sistema a Alpha Vantage
_TF_MAP = {
    "1d":  ("TIME_SERIES_DAILY_ADJUSTED", "Daily"),
    "4h":  ("TIME_SERIES_INTRADAY",       "4h"),
    "1h":  ("TIME_SERIES_INTRADAY",       "60min"),
}

# Activos que sabemos que AV cubre mejor que Yahoo
AV_PREFERRED = {
    "EURUSD=X", "GBPUSD=X", "USDJPY=X",  # forex
    "ES=F", "NQ=F", "CL=F", "GC=F",       # futuros
}

# Mapeo de tickers del sistema a tickers de Alpha Vantage
_AV_TICKER = {
    "EURUSD=X": "EUR",   # AV usa EUR para EUR/USD
    "GBPUSD=X": "GBP",
    "USDJPY=X": "JPY",
    "ES=F":     "SPY",   # aproximación con ETF
    "NQ=F":     "QQQ",
    "CL=F":     "USO",   # ETF de petróleo
    "GC=F":     "GLD",   # ETF de oro
}


def _get_api_key() -> str:
    key = os.environ.get("ALPHA_VANTAGE_API_KEY", "")
    if not key:
        env_path = Path(".env")
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                if line.startswith("ALPHA_VANTAGE_API_KEY="):
                    key = line.split("=", 1)[1].strip()
    return key


def fetch_daily(ticker: str, years: int = 3) -> pd.DataFrame:
    """Descarga datos diarios desde Alpha Vantage."""
    api_key = _get_api_key()
    if not api_key:
        logger.warning("ALPHA_VANTAGE_API_KEY no configurada")
        return pd.DataFrame()

    av_ticker = _AV_TICKER.get(ticker, ticker)
    outputsize = "full" if years > 1 else "compact"

    # Detectar si es forex
    if ticker in ("EURUSD=X", "GBPUSD=X", "USDJPY=X"):
        return _fetch_forex_daily(av_ticker, api_key, years)

    params = {
        "function": "TIME_SERIES_DAILY_ADJUSTED",
        "symbol": av_ticker,
        "outputsize": outputsize,
        "apikey": api_key,
        "datatype": "json",
    }

    try:
        resp = requests.get(AV_BASE, params=params, timeout=15)
        data = resp.json()

        if "Note" in data:
            logger.warning("Alpha Vantage rate limit: %s", data["Note"])
            return pd.DataFrame()

        if "Error Message" in data:
            logger.warning("Alpha Vantage error for %s: %s", av_ticker, data["Error Message"])
            return pd.DataFrame()

        ts = data.get("Time Series (Daily)", {})
        if not ts:
            logger.warning("Alpha Vantage: no data for %s", av_ticker)
            return pd.DataFrame()

        rows = []
        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - pd.Timedelta(days=365 * years)
        for date_str, vals in ts.items():
            dt = pd.to_datetime(date_str)
            if dt < cutoff:
                continue
            rows.append({
                "timestamp": dt,
                "open":   float(vals.get("1. open",   vals.get("1. open", 0))),
                "high":   float(vals.get("2. high",   0)),
                "low":    float(vals.get("3. low",    0)),
                "close":  float(vals.get("5. adjusted close", vals.get("4. close", 0))),
                "volume": float(vals.get("6. volume", vals.get("5. volume", 0))),
            })

        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows).set_index("timestamp").sort_index()
        logger.info("Alpha Vantage: %d barras para %s (mapped from %s)", len(df), av_ticker, ticker)
        return df

    except Exception as e:
        logger.error("Alpha Vantage fetch error for %s: %s", ticker, e)
        return pd.DataFrame()


def _fetch_forex_daily(from_symbol: str, api_key: str, years: int) -> pd.DataFrame:
    """Descarga datos diarios de forex desde Alpha Vantage."""
    params = {
        "function": "FX_DAILY",
        "from_symbol": from_symbol,
        "to_symbol": "USD",
        "outputsize": "full",
        "apikey": api_key,
    }

    try:
        resp = requests.get(AV_BASE, params=params, timeout=15)
        data = resp.json()

        if "Note" in data:
            logger.warning("Alpha Vantage rate limit")
            return pd.DataFrame()

        ts = data.get("Time Series FX (Daily)", {})
        if not ts:
            return pd.DataFrame()

        rows = []
        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - pd.Timedelta(days=365 * years)
        for date_str, vals in ts.items():
            dt = pd.to_datetime(date_str)
            if dt < cutoff:
                continue
            rows.append({
                "timestamp": dt,
                "open":   float(vals["1. open"]),
                "high":   float(vals["2. high"]),
                "low":    float(vals["3. low"]),
                "close":  float(vals["4. close"]),
                "volume": 0.0,
            })

        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows).set_index("timestamp").sort_index()
        logger.info("Alpha Vantage Forex: %d barras para %s/USD", len(df), from_symbol)
        return df

    except Exception as e:
        logger.error("Alpha Vantage forex error: %s", e)
        return pd.DataFrame()
