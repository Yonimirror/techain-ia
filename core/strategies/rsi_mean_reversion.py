from __future__ import annotations
from dataclasses import dataclass

from core.domain.entities import Signal, MarketData, PortfolioState
from core.domain.entities.signal import SignalDirection
from core.domain.entities.order import OrderSide
from core.interfaces.strategy_interface import IStrategy
from core.market_regime import RegimeDetector
from core.strategies.indicators import ema, rsi


@dataclass
class RSIMeanReversionConfig:
    rsi_period: int = 14
    oversold_threshold: float = 30.0
    overbought_threshold: float = 70.0
    extreme_oversold: float = 20.0    # very strong signal threshold
    extreme_overbought: float = 80.0
    stop_loss_pct: float = 5.0        # exit if price falls X% from entry (0 = disabled)
    filter_regime: bool = False       # only trade in ranging markets (ADX < 25)
    enable_short: bool = False        # generate SHORT signals on overbought
    ema_trend_filter: bool = False    # only LONG when price > EMA(ema_trend_period)
    ema_trend_period: int = 200       # EMA period for trend filter (default 200)


class RSIMeanReversionStrategy(IStrategy):
    """
    RSI Mean Reversion strategy.

    LONG when RSI < oversold_threshold (market oversold, expect bounce).
    SHORT when RSI > overbought_threshold (market overbought, expect pullback).

    Signal strength scales with how extreme the RSI reading is.
    """

    def __init__(self, config: RSIMeanReversionConfig | None = None) -> None:
        self._config = config or RSIMeanReversionConfig()

    @property
    def strategy_id(self) -> str:
        return "rsi_mean_reversion_v1"

    @property
    def version(self) -> str:
        return "1.0.0"

    def warmup_period(self) -> int:
        return self._config.rsi_period + 5

    def generate_signals(
        self,
        market_data: MarketData,
        portfolio_state: PortfolioState,
    ) -> list[Signal]:
        if len(market_data) < self.warmup_period():
            return []

        # Regime filter: RSI mean reversion only valid in ranging markets
        atr_ratio = 1.0
        if self._config.filter_regime:
            regime = RegimeDetector.detect(market_data)
            if regime is not None:
                atr_ratio = regime.atr_ratio
                if regime.is_trending:
                    return []

        closes = market_data.closes
        rsi_values = rsi(closes, self._config.rsi_period)

        # EMA trend filter: only trade LONG when price is above EMA(trend_period)
        trend_is_up = True
        if self._config.ema_trend_filter:
            ema_values = ema(closes, self._config.ema_trend_period)
            last_ema = ema_values[-1] if ema_values else None
            if last_ema is not None:
                trend_is_up = float(closes[-1]) > float(last_ema)

        latest_bar = market_data.latest

        if latest_bar is None:
            return []

        last_rsi = rsi_values[-1]
        if last_rsi is None:
            return []

        rsi_float = float(last_rsi)
        cfg = self._config
        signals: list[Signal] = []

        existing_pos = portfolio_state.get_position(market_data.symbol)
        current_price = float(latest_bar.close.value)

        if existing_pos:
            is_long = existing_pos.side == OrderSide.BUY
            is_short = existing_pos.side == OrderSide.SELL

            # Check stop loss first
            if cfg.stop_loss_pct > 0:
                entry_price = float(existing_pos.average_entry_price.value)
                if is_long:
                    loss_pct = (entry_price - current_price) / entry_price * 100
                else:
                    loss_pct = (current_price - entry_price) / entry_price * 100
                if loss_pct >= cfg.stop_loss_pct:
                    signals.append(Signal.create(
                        strategy_id=self.strategy_id,
                        symbol=market_data.symbol,
                        direction=SignalDirection.FLAT,
                        strength=1.0,
                        price=latest_bar.close,
                        timeframe=market_data.timeframe,
                        metadata={"rsi": rsi_float, "exit_reason": "stop_loss",
                                  "loss_pct": loss_pct},
                    ))
                    return signals

            # Exit LONG: RSI returned to overbought zone
            if is_long and rsi_float > cfg.overbought_threshold:
                signals.append(Signal.create(
                    strategy_id=self.strategy_id,
                    symbol=market_data.symbol,
                    direction=SignalDirection.FLAT,
                    strength=1.0,
                    price=latest_bar.close,
                    timeframe=market_data.timeframe,
                    metadata={"rsi": rsi_float, "exit_reason": "rsi_overbought"},
                ))

            # Exit SHORT: RSI returned to oversold zone
            elif is_short and rsi_float < cfg.oversold_threshold:
                signals.append(Signal.create(
                    strategy_id=self.strategy_id,
                    symbol=market_data.symbol,
                    direction=SignalDirection.FLAT,
                    strength=1.0,
                    price=latest_bar.close,
                    timeframe=market_data.timeframe,
                    metadata={"rsi": rsi_float, "exit_reason": "rsi_oversold"},
                ))

        else:
            # No position — check entry conditions

            # LONG: oversold (optionally filtered by EMA trend)
            if rsi_float < cfg.oversold_threshold and trend_is_up:
                raw = (cfg.oversold_threshold - rsi_float) / cfg.oversold_threshold
                strength = min(raw * 2.0, 1.0)
                signals.append(Signal.create(
                    strategy_id=self.strategy_id,
                    symbol=market_data.symbol,
                    direction=SignalDirection.LONG,
                    strength=strength,
                    price=latest_bar.close,
                    timeframe=market_data.timeframe,
                    metadata={"rsi": rsi_float, "condition": "oversold", "atr_ratio": atr_ratio},
                ))

            # SHORT: overbought (only if enabled)
            elif cfg.enable_short and rsi_float > cfg.overbought_threshold:
                raw = (rsi_float - cfg.overbought_threshold) / (100.0 - cfg.overbought_threshold)
                strength = min(raw * 2.0, 1.0)
                signals.append(Signal.create(
                    strategy_id=self.strategy_id,
                    symbol=market_data.symbol,
                    direction=SignalDirection.SHORT,
                    strength=strength,
                    price=latest_bar.close,
                    timeframe=market_data.timeframe,
                    metadata={"rsi": rsi_float, "condition": "overbought", "atr_ratio": atr_ratio},
                ))

        return signals
