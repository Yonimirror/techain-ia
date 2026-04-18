"""
Market Regime Detector.

Classifies the current market state from OHLCV bars using:
  - ADX (period=14): trend strength
      TRENDING  if ADX >= adx_trend_threshold (default 25)
      RANGING   if ADX <  adx_trend_threshold
  - ATR ratio (current ATR / rolling mean ATR, lookback=50):
      HIGH      if ratio > vol_high_threshold (default 1.5)
      LOW       if ratio < vol_low_threshold  (default 0.5)
      NORMAL    otherwise

Usage:
    regime = RegimeDetector.detect(market_data)
    if regime is None:
        # not enough bars — skip filter
    elif regime.is_trending:
        # activate trend-following strategies
"""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum

from core.domain.entities import MarketData


class TrendRegime(str, Enum):
    TRENDING = "trending"
    RANGING = "ranging"


class VolatilityRegime(str, Enum):
    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"


@dataclass(frozen=True)
class MarketRegime:
    trend: TrendRegime
    volatility: VolatilityRegime
    adx_value: float
    atr_ratio: float  # current ATR / rolling mean ATR (50 bars)

    @property
    def is_trending(self) -> bool:
        return self.trend == TrendRegime.TRENDING

    @property
    def is_ranging(self) -> bool:
        return self.trend == TrendRegime.RANGING

    @property
    def is_high_volatility(self) -> bool:
        return self.volatility == VolatilityRegime.HIGH

    def __str__(self) -> str:
        return f"{self.trend.value}/{self.volatility.value} adx={self.adx_value:.1f} atr_ratio={self.atr_ratio:.2f}"


class RegimeDetector:
    """
    Pure static classifier — no state, no I/O.

    detect() returns None if there aren't enough bars to compute
    a reliable regime (< adx_period * 3 bars).
    """

    @staticmethod
    def detect(
        market_data: MarketData,
        adx_period: int = 14,
        vol_lookback: int = 50,
        adx_trend_threshold: float = 25.0,
        vol_high_threshold: float = 1.5,
        vol_low_threshold: float = 0.5,
    ) -> MarketRegime | None:
        bars = market_data.bars
        min_bars = adx_period * 3
        if len(bars) < min_bars:
            return None

        # Lazy import breaks the circular dependency between market_regime and strategies
        from core.strategies.indicators import adx, atr  # noqa: PLC0415

        # Pre-convert to float — indicators use float internally, converting here
        # avoids repeated Decimal→float conversions inside adx()/atr()
        highs = [float(b.high.value) for b in bars]
        lows = [float(b.low.value) for b in bars]
        closes = [float(b.close.value) for b in bars]

        # ── ADX ──────────────────────────────────────────────────────────────
        adx_vals = adx(highs, lows, closes, period=adx_period)
        last_adx = next(
            (v for v in reversed(adx_vals) if v is not None), None
        )
        if last_adx is None:
            return None

        trend = TrendRegime.TRENDING if last_adx >= adx_trend_threshold else TrendRegime.RANGING

        # ── ATR ratio ────────────────────────────────────────────────────────
        atr_vals = atr(highs, lows, closes, period=adx_period)
        valid_atrs = [v for v in atr_vals if v is not None]
        if not valid_atrs:
            return None

        current_atr = valid_atrs[-1]
        recent_atrs = valid_atrs[-vol_lookback:] if len(valid_atrs) >= vol_lookback else valid_atrs
        mean_atr = sum(recent_atrs) / len(recent_atrs)
        atr_ratio = current_atr / mean_atr if mean_atr > 0 else 1.0

        if atr_ratio > vol_high_threshold:
            volatility = VolatilityRegime.HIGH
        elif atr_ratio < vol_low_threshold:
            volatility = VolatilityRegime.LOW
        else:
            volatility = VolatilityRegime.NORMAL

        return MarketRegime(
            trend=trend,
            volatility=volatility,
            adx_value=round(last_adx, 2),
            atr_ratio=round(atr_ratio, 3),
        )
