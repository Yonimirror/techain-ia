"""
Market Health Detector — checks exchange-level conditions before trading.

Uses Binance public API (no auth required) to detect:
1. Abnormal spread (illiquidity)
2. Collapsed volume (no participation)
3. Extreme funding rate (overleveraged market)

When health is DEGRADED or CRITICAL, the decision engine should
reduce position sizes or skip trading entirely.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum

import httpx

logger = logging.getLogger(__name__)

# Binance public endpoints (no API key needed)
BINANCE_TICKER_URL = "https://api.binance.com/api/v3/ticker/24hr"
BINANCE_FUNDING_URL = "https://fapi.binance.com/fapi/v1/fundingRate"
BINANCE_BOOK_TICKER_URL = "https://api.binance.com/api/v3/ticker/bookTicker"

# Symbol mapping: internal → Binance
BINANCE_SYMBOLS: dict[str, str] = {
    "BTC": "BTCUSDT",
    "ETH": "ETHUSDT",
    "SOL": "SOLUSDT",
}

# Futures symbols for funding rate
BINANCE_FUTURES: dict[str, str] = {
    "BTC": "BTCUSDT",
    "ETH": "ETHUSDT",
    "SOL": "SOLUSDT",
}


class HealthLevel(str, Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    CRITICAL = "CRITICAL"


@dataclass(frozen=True)
class MarketHealthStatus:
    """Result of a market health check."""
    level: HealthLevel
    spread_bps: float         # bid-ask spread in basis points
    volume_24h_usd: float     # 24h volume in USD
    funding_rate: float | None  # current funding rate (futures)
    issues: list[str] = field(default_factory=list)
    checked_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def sizing_factor(self) -> float:
        """Multiplier for position sizing: 1.0 = full, 0.5 = half, 0.0 = skip."""
        if self.level == HealthLevel.HEALTHY:
            return 1.0
        elif self.level == HealthLevel.DEGRADED:
            return 0.5
        return 0.0


@dataclass
class HealthThresholds:
    """Configurable thresholds for health checks."""
    max_spread_bps: float = 15.0           # > 15 bps = illiquid
    min_volume_24h_usd: float = 50_000_000  # < $50M = low participation
    extreme_funding_rate: float = 0.001     # |rate| > 0.1% = overleveraged
    critical_spread_bps: float = 50.0       # > 50 bps = critical
    critical_volume_24h_usd: float = 10_000_000  # < $10M = critical


class MarketHealthDetector:
    """
    Checks real-time market conditions via Binance public API.

    Usage:
        detector = MarketHealthDetector()
        status = await detector.check("BTC")
        if status.level == HealthLevel.CRITICAL:
            # skip trading
        elif status.level == HealthLevel.DEGRADED:
            # reduce position size by status.sizing_factor
    """

    def __init__(
        self,
        thresholds: HealthThresholds | None = None,
        cache_seconds: int = 60,
    ) -> None:
        self._thresholds = thresholds or HealthThresholds()
        self._cache_seconds = cache_seconds
        self._cache: dict[str, MarketHealthStatus] = {}

    async def check(self, symbol: str) -> MarketHealthStatus:
        """
        Check market health for a symbol. Results are cached for cache_seconds.
        Returns HEALTHY with defaults if API calls fail (fail-open for paper trading).
        """
        cached = self._cache.get(symbol)
        if cached and (datetime.now(timezone.utc) - cached.checked_at).total_seconds() < self._cache_seconds:
            return cached

        binance_symbol = BINANCE_SYMBOLS.get(symbol, f"{symbol}USDT")
        futures_symbol = BINANCE_FUTURES.get(symbol)

        spread_bps = 0.0
        volume_24h = 0.0
        funding_rate: float | None = None
        issues: list[str] = []

        async with httpx.AsyncClient(timeout=5.0) as client:
            # 1. Spread from book ticker
            try:
                resp = await client.get(
                    BINANCE_BOOK_TICKER_URL,
                    params={"symbol": binance_symbol},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    bid = Decimal(data["bidPrice"])
                    ask = Decimal(data["askPrice"])
                    mid = (bid + ask) / 2
                    if mid > 0:
                        spread_bps = float((ask - bid) / mid * 10000)
            except Exception as exc:
                logger.warning("Failed to fetch spread for %s: %s", symbol, exc)

            # 2. Volume from 24hr ticker
            try:
                resp = await client.get(
                    BINANCE_TICKER_URL,
                    params={"symbol": binance_symbol},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    volume_24h = float(data.get("quoteVolume", 0))
            except Exception as exc:
                logger.warning("Failed to fetch volume for %s: %s", symbol, exc)

            # 3. Funding rate from futures
            if futures_symbol:
                try:
                    resp = await client.get(
                        BINANCE_FUNDING_URL,
                        params={"symbol": futures_symbol, "limit": 1},
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        if data:
                            funding_rate = float(data[-1]["fundingRate"])
                except Exception as exc:
                    logger.warning("Failed to fetch funding for %s: %s", symbol, exc)

        # Evaluate thresholds
        t = self._thresholds

        if spread_bps >= t.critical_spread_bps:
            issues.append(f"CRITICAL spread: {spread_bps:.1f} bps")
        elif spread_bps >= t.max_spread_bps:
            issues.append(f"High spread: {spread_bps:.1f} bps")

        if 0 < volume_24h < t.critical_volume_24h_usd:
            issues.append(f"CRITICAL volume: ${volume_24h:,.0f}")
        elif 0 < volume_24h < t.min_volume_24h_usd:
            issues.append(f"Low volume: ${volume_24h:,.0f}")

        if funding_rate is not None and abs(funding_rate) >= t.extreme_funding_rate:
            direction = "long-heavy" if funding_rate > 0 else "short-heavy"
            issues.append(f"Extreme funding: {funding_rate:.4f} ({direction})")

        # Determine level
        has_critical = any("CRITICAL" in i for i in issues)
        if has_critical:
            level = HealthLevel.CRITICAL
        elif issues:
            level = HealthLevel.DEGRADED
        else:
            level = HealthLevel.HEALTHY

        status = MarketHealthStatus(
            level=level,
            spread_bps=spread_bps,
            volume_24h_usd=volume_24h,
            funding_rate=funding_rate,
            issues=issues,
        )

        self._cache[symbol] = status
        logger.info("Market health %s: %s | %s", symbol, level.value, issues or "OK")
        return status

    def invalidate_cache(self, symbol: str | None = None) -> None:
        """Clear cached results."""
        if symbol:
            self._cache.pop(symbol, None)
        else:
            self._cache.clear()
