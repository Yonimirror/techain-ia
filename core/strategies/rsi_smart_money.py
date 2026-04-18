"""
RSI + Smart Money Strategy.

Combina RSI Mean Reversion con confirmación de Smart Money (ballenas + order flow).

Lógica de entrada:
1. RSI < oversold_threshold → señal de rebote técnico
2. Smart Money bias → confirma o penaliza la señal

Resultado:
- RSI oversold + Smart Money bullish  → entrada fuerte (1.5x tamaño)
- RSI oversold + Smart Money neutral  → entrada normal (0.7x tamaño)
- RSI oversold + Smart Money bearish  → NO entra (ballenas vendiendo = trampa)

Esto evita comprar "cuchillos que caen" cuando las instituciones están saliendo.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta

from core.domain.entities import Signal, MarketData, PortfolioState
from core.domain.entities.signal import SignalDirection
from core.interfaces.strategy_interface import IStrategy
from core.strategies.indicators import ema, rsi

logger = logging.getLogger(__name__)


@dataclass
class RSISmartMoneyConfig:
    # RSI params
    rsi_period: int = 7
    oversold_threshold: float = 35.0
    overbought_threshold: float = 60.0
    stop_loss_pct: float = 5.0

    # EMA trend filter
    ema_trend_filter: bool = True
    ema_trend_period: int = 200

    # Smart Money params
    smart_money_enabled: bool = True
    smart_money_lookback_hours: int = 24
    smart_money_max_age_hours: int = 4  # señal stale si tiene más de N horas
    min_whale_net_flow_usd: float = 500_000   # mínimo flujo neto alcista para confirmar
    skip_on_bearish_smart_money: bool = True  # no entrar si ballenas venden

    # Position sizing override basado en Smart Money conviction
    base_position_pct: float = 8.0            # % capital sin Smart Money
    max_position_pct: float = 15.0            # % capital con alta convicción SM
    min_position_pct: float = 3.0             # % capital con baja convicción SM


class RSISmartMoneyStrategy(IStrategy):
    """
    RSI Mean Reversion con filtro de Smart Money.

    Es una función pura: no hace I/O directamente.
    El SmartMoneySignal se inyecta externamente (desde el trader service).

    Flujo:
    1. Trader service obtiene SmartMoneySignal antes de llamar generate_signals()
    2. Inyecta el signal via set_smart_money_context()
    3. generate_signals() usa el signal para filtrar y escalar
    """

    def __init__(self, config: RSISmartMoneyConfig | None = None) -> None:
        self._config = config or RSISmartMoneyConfig()
        self._smart_money_signal = None        # inyectado externamente
        self._smart_money_set_at: datetime | None = None  # cuándo se inyectó

    @property
    def strategy_id(self) -> str:
        return "rsi_smart_money_v1"

    @property
    def version(self) -> str:
        return "1.0.0"

    def warmup_period(self) -> int:
        base = self._config.rsi_period + 5
        if self._config.ema_trend_filter:
            return max(base, self._config.ema_trend_period + 5)
        return base

    def set_smart_money_context(self, signal) -> None:
        """
        Inyecta el SmartMoneySignal antes de generate_signals().
        Llamado por el trader service, mantiene la pureza de la estrategia.
        """
        self._smart_money_signal = signal
        self._smart_money_set_at = datetime.now(timezone.utc)

    def generate_signals(
        self,
        market_data: MarketData,
        portfolio_state: PortfolioState,
    ) -> list[Signal]:
        if len(market_data) < self.warmup_period():
            return []

        cfg = self._config
        latest_bar = market_data.bars[-1]
        closes = [float(b.close.value) for b in market_data.bars]
        current_price = closes[-1]
        rsi_values = rsi(closes, cfg.rsi_period)
        if not rsi_values:
            return []
        current_rsi = rsi_values[-1]

        # EMA trend filter
        trend_is_up = True
        if cfg.ema_trend_filter:
            ema_values = ema(closes, cfg.ema_trend_period)
            last_ema = ema_values[-1] if ema_values else None
            if last_ema is not None:
                trend_is_up = current_price > float(last_ema)

        # Posición actual
        position = portfolio_state.get_position(market_data.symbol)
        has_position = position is not None and position.quantity.value > 0

        signals = []

        # ── LÓGICA DE SALIDA (prioridad sobre entrada) ─────────────────────
        if has_position:
            # Exit: RSI overbought
            if current_rsi > cfg.overbought_threshold:
                signals.append(Signal.create(
                    strategy_id=self.strategy_id,
                    symbol=market_data.symbol,
                    direction=SignalDirection.FLAT,
                    strength=1.0,
                    price=latest_bar.close,
                    timeframe=market_data.timeframe,
                    metadata={
                        "reason": "RSI overbought exit",
                        "rsi": current_rsi,
                        "smart_money": self._sm_summary(),
                    },
                ))
                return signals

            # Exit: Stop loss
            if cfg.stop_loss_pct > 0:
                entry = float(position.average_entry_price.value)
                loss_pct = (current_price - entry) / entry * 100
                if loss_pct <= -cfg.stop_loss_pct:
                    signals.append(Signal.create(
                        strategy_id=self.strategy_id,
                        symbol=market_data.symbol,
                        direction=SignalDirection.FLAT,
                        strength=1.0,
                        price=latest_bar.close,
                        timeframe=market_data.timeframe,
                        metadata={
                            "reason": f"Stop loss hit: {loss_pct:.1f}%",
                            "smart_money": self._sm_summary(),
                        },
                    ))
                    return signals

            return signals  # Mantener posición abierta

        # ── LÓGICA DE ENTRADA ───────────────────────────────────────────────
        if current_rsi >= cfg.oversold_threshold:
            return []  # RSI no oversold, no hay señal

        if not trend_is_up:
            return []  # Precio por debajo de EMA200

        # Calcular fuerza base del RSI (×2.0 igual que RSIMeanReversionStrategy)
        raw_strength = (cfg.oversold_threshold - current_rsi) / cfg.oversold_threshold
        rsi_strength = min(1.0, max(0.0, raw_strength * 2.0))

        # ── FILTRO SMART MONEY ──────────────────────────────────────────────
        sm = self._smart_money_signal if not self._is_sm_stale() else None
        sm_multiplier = 1.0
        sm_note = "Sin datos Smart Money (entrada normal)"

        if sm and cfg.smart_money_enabled:
            # Bloquear si ballenas están vendiendo
            if cfg.skip_on_bearish_smart_money and sm.should_skip:
                logger.info(
                    "RSI oversold BLOQUEADO por Smart Money bearish: %s (conviction=%.2f)",
                    sm.reason, sm.conviction,
                )
                return []  # No entrar — ballenas venden

            sm_multiplier = sm.position_size_multiplier
            sm_note = sm.reason

            logger.info(
                "RSI oversold CONFIRMADO por Smart Money: bias=%s conviction=%.2f multiplier=%.1fx | %s",
                sm.bias, sm.conviction, sm_multiplier, sm.reason,
            )

        # Fuerza final = RSI strength × Smart Money multiplier
        final_strength = min(1.0, rsi_strength * sm_multiplier)

        signals.append(Signal.create(
            strategy_id=self.strategy_id,
            symbol=market_data.symbol,
            direction=SignalDirection.LONG,
            strength=final_strength,
            price=latest_bar.close,
            timeframe=market_data.timeframe,
            metadata={
                "rsi": current_rsi,
                "rsi_strength": rsi_strength,
                "sm_bias": sm.bias if sm else "no_data",
                "sm_conviction": sm.conviction if sm else 0.0,
                "sm_multiplier": sm_multiplier,
                "sm_reason": sm_note,
                "price_vs_ema200": "above" if trend_is_up else "below",
            },
        ))

        return signals

    def _is_sm_stale(self) -> bool:
        """
        Devuelve True si el SmartMoneySignal fue inyectado hace más de
        smart_money_max_age_hours horas, o si nunca fue inyectado.

        Una señal stale se trata como "sin datos" → entrada con tamaño normal
        en lugar de bloquear o amplificar basándose en información obsoleta.
        """
        if self._smart_money_set_at is None:
            return False  # nunca inyectado: no hay señal que sea stale
        age = datetime.now(timezone.utc) - self._smart_money_set_at
        max_age = timedelta(hours=self._config.smart_money_max_age_hours)
        if age > max_age:
            logger.warning(
                "SmartMoney signal STALE (age=%s > max=%s) — ignorando filtro SM",
                age, max_age,
            )
            return True
        return False

    def _sm_summary(self) -> str:
        if self._is_sm_stale():
            return "stale"
        if not self._smart_money_signal:
            return "no_data"
        sm = self._smart_money_signal
        return f"bias={sm.bias.value} conviction={sm.conviction:.2f}"
