from __future__ import annotations
from dataclasses import dataclass

from core.domain.entities import Signal, MarketData, PortfolioState
from core.domain.entities.signal import SignalDirection
from core.domain.entities.order import OrderSide
from core.domain.value_objects import Timeframe
from core.interfaces.strategy_interface import IStrategy
from core.market_regime import RegimeDetector
from core.strategies.indicators import ema, crossover, crossunder


@dataclass
class EMACrossoverConfig:
    fast_period: int = 9
    slow_period: int = 21
    signal_period: int = 5       # confirmation EMA on the fast/slow diff
    min_strength_threshold: float = 0.3
    stop_loss_pct: float = 5.0   # exit if price falls X% from entry (0 = disabled)
    filter_regime: bool = False  # only trade in trending markets (ADX >= 25)
    enable_short: bool = False   # generate SHORT signals on cross-under


class EMACrossoverStrategy(IStrategy):
    """
    EMA Crossover strategy.

    Generates LONG signal when fast EMA crosses above slow EMA,
    SHORT signal when fast EMA crosses below slow EMA.

    Signal strength is proportional to the relative distance between EMAs.
    """

    def __init__(self, config: EMACrossoverConfig | None = None) -> None:
        self._config = config or EMACrossoverConfig()

    @property
    def strategy_id(self) -> str:
        return "ema_crossover_v1"

    @property
    def version(self) -> str:
        return "1.0.0"

    def warmup_period(self) -> int:
        return self._config.slow_period + 5

    def generate_signals(
        self,
        market_data: MarketData,
        portfolio_state: PortfolioState,
    ) -> list[Signal]:
        if len(market_data) < self.warmup_period():
            return []

        # Regime filter: EMA crossover only valid in trending markets
        atr_ratio = 1.0
        if self._config.filter_regime:
            regime = RegimeDetector.detect(market_data)
            if regime is not None:
                atr_ratio = regime.atr_ratio
                if regime.is_ranging:
                    return []

        closes = market_data.closes
        fast = ema(closes, self._config.fast_period)
        slow = ema(closes, self._config.slow_period)

        signals: list[Signal] = []
        latest_bar = market_data.latest
        if latest_bar is None:
            return []

        last_idx = len(closes) - 1

        # Check crossover at the latest bar only
        cross_up = crossover(fast, slow)
        cross_dn = crossunder(fast, slow)

        f_val = fast[last_idx]
        s_val = slow[last_idx]
        if f_val is None or s_val is None:
            return []

        # Signal strength: normalized separation between fast and slow
        separation = abs(f_val - s_val) / s_val
        strength = min(separation * 100, 1.0)  # cap at 1.0

        existing_pos = portfolio_state.get_position(market_data.symbol)
        current_price = float(latest_bar.close.value)

        # Check stop loss on existing position first
        if existing_pos and self._config.stop_loss_pct > 0:
            entry_price = float(existing_pos.average_entry_price.value)
            is_long = existing_pos.side == OrderSide.BUY
            if is_long:
                loss_pct = (entry_price - current_price) / entry_price * 100
            else:
                loss_pct = (current_price - entry_price) / entry_price * 100
            if loss_pct >= self._config.stop_loss_pct:
                signals.append(Signal.create(
                    strategy_id=self.strategy_id,
                    symbol=market_data.symbol,
                    direction=SignalDirection.FLAT,
                    strength=1.0,
                    price=latest_bar.close,
                    timeframe=market_data.timeframe,
                    metadata={
                        "fast_ema": f_val,
                        "slow_ema": s_val,
                        "exit_reason": "stop_loss",
                        "loss_pct": loss_pct,
                    },
                ))
                return signals

        if cross_up[last_idx] and strength >= self._config.min_strength_threshold:
            if existing_pos and existing_pos.side == OrderSide.SELL:
                # Close SHORT position
                signals.append(Signal.create(
                    strategy_id=self.strategy_id,
                    symbol=market_data.symbol,
                    direction=SignalDirection.FLAT,
                    strength=1.0,
                    price=latest_bar.close,
                    timeframe=market_data.timeframe,
                    metadata={
                        "fast_ema": f_val,
                        "slow_ema": s_val,
                        "exit_reason": "crossover_close_short",
                    },
                ))
            elif not existing_pos:
                # No position — enter LONG
                signals.append(Signal.create(
                    strategy_id=self.strategy_id,
                    symbol=market_data.symbol,
                    direction=SignalDirection.LONG,
                    strength=strength,
                    price=latest_bar.close,
                    timeframe=market_data.timeframe,
                    metadata={
                        "fast_ema": f_val,
                        "slow_ema": s_val,
                        "separation_pct": separation * 100,
                        "atr_ratio": atr_ratio,
                    },
                ))

        elif cross_dn[last_idx]:
            if existing_pos and existing_pos.side == OrderSide.BUY:
                # Close LONG position
                signals.append(Signal.create(
                    strategy_id=self.strategy_id,
                    symbol=market_data.symbol,
                    direction=SignalDirection.FLAT,
                    strength=1.0,
                    price=latest_bar.close,
                    timeframe=market_data.timeframe,
                    metadata={
                        "fast_ema": f_val,
                        "slow_ema": s_val,
                        "exit_reason": "crossunder",
                    },
                ))
            elif not existing_pos and self._config.enable_short and strength >= self._config.min_strength_threshold:
                # No position — enter SHORT
                signals.append(Signal.create(
                    strategy_id=self.strategy_id,
                    symbol=market_data.symbol,
                    direction=SignalDirection.SHORT,
                    strength=strength,
                    price=latest_bar.close,
                    timeframe=market_data.timeframe,
                    metadata={
                        "fast_ema": f_val,
                        "slow_ema": s_val,
                        "separation_pct": separation * 100,
                        "atr_ratio": atr_ratio,
                    },
                ))

        return signals
