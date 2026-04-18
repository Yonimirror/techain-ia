from __future__ import annotations
from dataclasses import dataclass

from core.domain.entities import Signal, MarketData, PortfolioState
from core.domain.entities.signal import SignalDirection
from core.domain.entities.order import OrderSide
from core.interfaces.strategy_interface import IStrategy
from core.market_regime import RegimeDetector
from core.strategies.indicators import bollinger_bands, rsi


@dataclass
class BollingerReversionConfig:
    bb_period: int = 20
    bb_std: float = 2.0
    rsi_period: int = 14
    rsi_oversold: float = 40.0
    rsi_overbought: float = 60.0
    stop_loss_pct: float = 5.0
    enable_short: bool = False
    filter_regime: bool = False


class BollingerReversionStrategy(IStrategy):
    """
    Bollinger Band Mean Reversion strategy.

    LONG when price touches lower band + RSI confirms oversold.
    SHORT (optional) when price touches upper band + RSI confirms overbought.
    Exit when price reaches opposite band or stop loss triggers.
    """

    def __init__(self, config: BollingerReversionConfig | None = None) -> None:
        self._config = config or BollingerReversionConfig()

    @property
    def strategy_id(self) -> str:
        return "bollinger_reversion_v1"

    @property
    def version(self) -> str:
        return "1.0.0"

    def warmup_period(self) -> int:
        return max(self._config.bb_period, self._config.rsi_period) + 5

    def generate_signals(
        self,
        market_data: MarketData,
        portfolio_state: PortfolioState,
    ) -> list[Signal]:
        if len(market_data) < self.warmup_period():
            return []

        atr_ratio = 1.0
        if self._config.filter_regime:
            regime = RegimeDetector.detect(market_data)
            if regime is not None:
                atr_ratio = regime.atr_ratio
                if regime.is_trending:
                    return []

        cfg = self._config
        # Limit to last `lookback` bars: indicators only need their period × 4
        # for convergence. Avoids O(n × period) recompute on full 500-bar window.
        lookback = max(cfg.bb_period, cfg.rsi_period) * 4
        closes = market_data.closes
        if len(closes) > lookback:
            closes = closes[-lookback:]

        upper, middle, lower = bollinger_bands(closes, cfg.bb_period, cfg.bb_std)
        rsi_values = rsi(closes, cfg.rsi_period)

        latest_bar = market_data.latest
        if latest_bar is None:
            return []

        last_idx = len(closes) - 1
        last_upper = upper[last_idx]
        last_middle = middle[last_idx]
        last_lower = lower[last_idx]
        last_rsi = rsi_values[last_idx]

        if any(v is None for v in (last_upper, last_middle, last_lower, last_rsi)):
            return []

        current_price = float(latest_bar.close.value)
        rsi_float = float(last_rsi)
        signals: list[Signal] = []

        existing_pos = portfolio_state.get_position(market_data.symbol)

        if existing_pos:
            is_long = existing_pos.side == OrderSide.BUY
            is_short = existing_pos.side == OrderSide.SELL

            # Stop loss check
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
                        metadata={"exit_reason": "stop_loss", "loss_pct": loss_pct,
                                  "rsi": rsi_float},
                    ))
                    return signals

            # Exit LONG when price reaches upper band
            if is_long and current_price >= last_upper:
                signals.append(Signal.create(
                    strategy_id=self.strategy_id,
                    symbol=market_data.symbol,
                    direction=SignalDirection.FLAT,
                    strength=1.0,
                    price=latest_bar.close,
                    timeframe=market_data.timeframe,
                    metadata={"exit_reason": "upper_band", "rsi": rsi_float,
                              "upper_band": last_upper},
                ))

            # Exit SHORT when price reaches lower band
            elif is_short and current_price <= last_lower:
                signals.append(Signal.create(
                    strategy_id=self.strategy_id,
                    symbol=market_data.symbol,
                    direction=SignalDirection.FLAT,
                    strength=1.0,
                    price=latest_bar.close,
                    timeframe=market_data.timeframe,
                    metadata={"exit_reason": "lower_band", "rsi": rsi_float,
                              "lower_band": last_lower},
                ))

        else:
            # No position — check for entry signals
            band_width = last_upper - last_lower
            if band_width <= 0:
                return []

            # LONG: price at or below lower band + RSI confirms oversold
            if current_price <= last_lower and rsi_float < cfg.rsi_oversold:
                distance_below = (last_lower - current_price) / band_width
                strength = min((distance_below + 0.1) * 2.0, 1.0)
                signals.append(Signal.create(
                    strategy_id=self.strategy_id,
                    symbol=market_data.symbol,
                    direction=SignalDirection.LONG,
                    strength=max(strength, 0.3),
                    price=latest_bar.close,
                    timeframe=market_data.timeframe,
                    metadata={
                        "condition": "lower_band_touch",
                        "rsi": rsi_float,
                        "lower_band": last_lower,
                        "upper_band": last_upper,
                        "atr_ratio": atr_ratio,
                    },
                ))

            # SHORT: price at or above upper band + RSI confirms overbought
            elif (cfg.enable_short
                  and current_price >= last_upper
                  and rsi_float > cfg.rsi_overbought):
                distance_above = (current_price - last_upper) / band_width
                strength = min((distance_above + 0.1) * 2.0, 1.0)
                signals.append(Signal.create(
                    strategy_id=self.strategy_id,
                    symbol=market_data.symbol,
                    direction=SignalDirection.SHORT,
                    strength=max(strength, 0.3),
                    price=latest_bar.close,
                    timeframe=market_data.timeframe,
                    metadata={
                        "condition": "upper_band_touch",
                        "rsi": rsi_float,
                        "lower_band": last_lower,
                        "upper_band": last_upper,
                        "atr_ratio": atr_ratio,
                    },
                ))

        return signals
