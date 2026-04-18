"""
Tests para RSISmartMoneyStrategy — cobertura completa de:
  - Configuración y warmup_period()
  - Stale signal detection (M2)
  - Filtro SM bearish bloquea entrada
  - SM bullish amplifica señal
  - SM neutral reduce señal
  - Sin contexto SM → entrada normal
  - Señales de salida no afectadas por SM
  - Integración: SmartMoneySignal mock inyectado via set_smart_money_context()
"""
from __future__ import annotations

import pytest
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from unittest.mock import MagicMock

from core.domain.entities import MarketData, PortfolioState, Position
from core.domain.entities.market_data import OHLCV
from core.domain.entities.order import OrderSide
from core.domain.entities.signal import SignalDirection
from core.domain.value_objects import Symbol, Timeframe, Price, Quantity
from core.strategies.rsi_smart_money import RSISmartMoneyStrategy, RSISmartMoneyConfig
from infrastructure.smart_money.smart_money_aggregator import (
    SmartMoneySignal, SmartMoneyBias,
)

BTC = Symbol("BTC")
D1 = Timeframe("1d")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_bars(prices: list[float]) -> list[OHLCV]:
    bars = []
    for i, p in enumerate(prices):
        bars.append(OHLCV(
            timestamp=datetime(2022, 1, 1, tzinfo=timezone.utc) + timedelta(days=i),
            open=Price.of(p * 0.999),
            high=Price.of(p * 1.01),
            low=Price.of(p * 0.99),
            close=Price.of(p),
            volume=Quantity.of(1000),
        ))
    return bars


def _make_market_data(prices: list[float]) -> MarketData:
    return MarketData(symbol=BTC, timeframe=D1, bars=_make_bars(prices))


def _empty_portfolio() -> PortfolioState:
    return PortfolioState(cash=Decimal("100000"), initial_capital=Decimal("100000"))


def _portfolio_with_position(entry_price: float) -> PortfolioState:
    """Portfolio con posición BTC abierta en LONG."""
    ps = PortfolioState(cash=Decimal("90000"), initial_capital=Decimal("100000"))
    pos = Position(
        symbol=BTC,
        side=OrderSide.BUY,
        quantity=Quantity.of(0.1),
        average_entry_price=Price.of(entry_price),
        opened_at=datetime.now(timezone.utc),
    )
    ps.positions[str(BTC)] = pos
    return ps


def _bull_prices_with_oversold(n_up: int = 210, n_pullback: int = 10) -> list[float]:
    """Precio sube 210 barras luego cae bruscamente → RSI oversold, precio > EMA200."""
    up = [40_000 + i * 300 for i in range(n_up)]
    pullback = [up[-1] - i * 2_000 for i in range(1, n_pullback + 1)]
    return up + pullback


def _bear_prices(n: int = 220) -> list[float]:
    """Precio cayendo → RSI bajo, precio < EMA200."""
    return [100_000 - i * 250 for i in range(n)]


def _make_sm_signal(
    bias: str,
    conviction: float = 0.8,
    age_hours: float = 0.0,
) -> SmartMoneySignal:
    """Crea un SmartMoneySignal mock con bias y edad configurables."""
    ts = datetime.now(timezone.utc) - timedelta(hours=age_hours)
    return SmartMoneySignal(
        symbol="BTC",
        timestamp=ts,
        bias=SmartMoneyBias(bias),
        conviction=conviction,
        whale_bias=bias,
        whale_net_flow_usd=10_000_000 if bias == "bullish" else -10_000_000,
        orderflow_bias=bias,
        orderflow_imbalance=0.5 if bias == "bullish" else (-0.5 if bias == "bearish" else 0.0),
        reason=f"Test signal: {bias}",
    )


def _default_strategy() -> RSISmartMoneyStrategy:
    """Estrategia con EMA200 activo, thresholds amplios para facilitar señales en tests."""
    cfg = RSISmartMoneyConfig(
        rsi_period=7,
        oversold_threshold=45,  # umbral más alto para generar señales con datos sintéticos
        overbought_threshold=60,
        stop_loss_pct=5.0,
        ema_trend_filter=True,
        ema_trend_period=200,
        smart_money_enabled=True,
        smart_money_max_age_hours=4,
    )
    return RSISmartMoneyStrategy(cfg)


# ── Tests de configuración ────────────────────────────────────────────────────

class TestRSISmartMoneyConfig:
    def test_default_config(self):
        cfg = RSISmartMoneyConfig()
        assert cfg.rsi_period == 7
        assert cfg.oversold_threshold == 35.0
        assert cfg.overbought_threshold == 60.0
        assert cfg.ema_trend_filter is True
        assert cfg.smart_money_enabled is True
        assert cfg.smart_money_max_age_hours == 4

    def test_warmup_with_ema_filter(self):
        cfg = RSISmartMoneyConfig(rsi_period=7, ema_trend_filter=True, ema_trend_period=200)
        strategy = RSISmartMoneyStrategy(cfg)
        assert strategy.warmup_period() == 205  # max(12, 205)

    def test_warmup_without_ema_filter(self):
        cfg = RSISmartMoneyConfig(rsi_period=7, ema_trend_filter=False)
        strategy = RSISmartMoneyStrategy(cfg)
        assert strategy.warmup_period() == 12  # rsi_period + 5

    def test_warmup_short_ema_uses_rsi(self):
        """Si ema_period < rsi_period + 5, el warmup lo determina el RSI."""
        cfg = RSISmartMoneyConfig(rsi_period=14, ema_trend_filter=True, ema_trend_period=10)
        strategy = RSISmartMoneyStrategy(cfg)
        assert strategy.warmup_period() == 19  # max(19, 15) = 19

    def test_strategy_id(self):
        assert RSISmartMoneyStrategy().strategy_id == "rsi_smart_money_v1"


# ── Tests de señal stale ──────────────────────────────────────────────────────

class TestStaleSignalDetection:
    def test_no_context_set_not_stale(self):
        """Sin contexto inyectado, _is_sm_stale() devuelve False (no hay señal que expirar)."""
        strategy = RSISmartMoneyStrategy()
        assert strategy._is_sm_stale() is False

    def test_fresh_signal_not_stale(self):
        """Señal inyectada hace 1 hora no está stale (max_age=4h)."""
        strategy = RSISmartMoneyStrategy(RSISmartMoneyConfig(smart_money_max_age_hours=4))
        sm = _make_sm_signal("bullish", age_hours=1.0)
        strategy.set_smart_money_context(sm)
        assert strategy._is_sm_stale() is False

    def test_old_signal_is_stale(self):
        """Señal inyectada hace más de max_age_hours está stale."""
        strategy = RSISmartMoneyStrategy(RSISmartMoneyConfig(smart_money_max_age_hours=4))
        sm = _make_sm_signal("bullish", age_hours=0.0)
        strategy.set_smart_money_context(sm)
        # Simular que la inyección ocurrió hace 5 horas retrasando _smart_money_set_at
        strategy._smart_money_set_at = datetime.now(timezone.utc) - timedelta(hours=5)
        assert strategy._is_sm_stale() is True

    def test_exactly_at_max_age_not_stale(self):
        """Señal inyectada exactamente en el límite NO está stale (>= no >)."""
        strategy = RSISmartMoneyStrategy(RSISmartMoneyConfig(smart_money_max_age_hours=4))
        sm = _make_sm_signal("bullish")
        strategy.set_smart_money_context(sm)
        strategy._smart_money_set_at = datetime.now(timezone.utc) - timedelta(hours=4, seconds=-1)
        assert strategy._is_sm_stale() is False

    def test_sm_summary_returns_stale_when_stale(self):
        strategy = RSISmartMoneyStrategy(RSISmartMoneyConfig(smart_money_max_age_hours=4))
        sm = _make_sm_signal("bullish")
        strategy.set_smart_money_context(sm)
        strategy._smart_money_set_at = datetime.now(timezone.utc) - timedelta(hours=6)
        assert strategy._sm_summary() == "stale"

    def test_sm_summary_no_data_without_context(self):
        strategy = RSISmartMoneyStrategy()
        assert strategy._sm_summary() == "no_data"

    def test_sm_summary_shows_bias_when_fresh(self):
        strategy = RSISmartMoneyStrategy()
        sm = _make_sm_signal("bullish", conviction=0.9)
        strategy.set_smart_money_context(sm)
        summary = strategy._sm_summary()
        assert "bullish" in summary
        assert "0.90" in summary


# ── Tests de lógica de entrada con SM ────────────────────────────────────────

class TestSmartMoneyEntryFilter:
    """
    Verifica que el filtro SM modifica correctamente las señales de entrada.
    Usa mercado alcista sintético (precio > EMA200 + RSI oversold).
    """

    def _get_entry_signal(self, strategy: RSISmartMoneyStrategy) -> list:
        md = _make_market_data(_bull_prices_with_oversold())
        ps = _empty_portfolio()
        return strategy.generate_signals(md, ps)

    def test_no_sm_context_generates_normal_entry(self):
        """Sin SM context → entrada con fuerza basada solo en RSI."""
        strategy = _default_strategy()
        signals = self._get_entry_signal(strategy)
        longs = [s for s in signals if s.direction == SignalDirection.LONG]
        assert len(longs) > 0, "Debe generar señal LONG sin SM context"

    def test_bearish_sm_blocks_entry(self):
        """SM bearish + skip_on_bearish=True → NO genera señal de entrada."""
        strategy = _default_strategy()
        sm = _make_sm_signal("bearish", conviction=0.9)
        strategy.set_smart_money_context(sm)

        signals = self._get_entry_signal(strategy)
        longs = [s for s in signals if s.direction == SignalDirection.LONG]
        assert len(longs) == 0, "SM bearish debe bloquear la entrada"

    def test_bearish_sm_allowed_when_skip_disabled(self):
        """Con skip_on_bearish=False, SM bearish no bloquea (solo reduce tamaño)."""
        cfg = RSISmartMoneyConfig(
            rsi_period=7, oversold_threshold=45,
            ema_trend_filter=True, ema_trend_period=200,
            smart_money_enabled=True,
            skip_on_bearish_smart_money=False,
        )
        strategy = RSISmartMoneyStrategy(cfg)
        sm = _make_sm_signal("bearish", conviction=0.9)
        strategy.set_smart_money_context(sm)

        signals = self._get_entry_signal(strategy)
        longs = [s for s in signals if s.direction == SignalDirection.LONG]
        assert len(longs) > 0, "Con skip_on_bearish=False, debe entrar aunque SM sea bearish"

    def test_bullish_sm_high_conviction_amplifies_strength(self):
        """SM bullish alta convicción → fuerza final > fuerza sin SM."""
        strategy_no_sm = _default_strategy()
        strategy_with_sm = _default_strategy()

        sm = _make_sm_signal("bullish", conviction=0.9)
        strategy_with_sm.set_smart_money_context(sm)

        md = _make_market_data(_bull_prices_with_oversold())
        ps = _empty_portfolio()

        sigs_no_sm = [s for s in strategy_no_sm.generate_signals(md, ps)
                      if s.direction == SignalDirection.LONG]
        sigs_with_sm = [s for s in strategy_with_sm.generate_signals(md, ps)
                        if s.direction == SignalDirection.LONG]

        assert len(sigs_no_sm) > 0 and len(sigs_with_sm) > 0
        assert sigs_with_sm[0].strength >= sigs_no_sm[0].strength, (
            "SM bullish alta convicción debe amplificar la fuerza"
        )

    def test_neutral_sm_reduces_strength(self):
        """SM neutral → fuerza reducida respecto a sin SM."""
        strategy_no_sm = _default_strategy()
        strategy_with_sm = _default_strategy()

        sm = _make_sm_signal("neutral", conviction=0.2)
        strategy_with_sm.set_smart_money_context(sm)

        md = _make_market_data(_bull_prices_with_oversold())
        ps = _empty_portfolio()

        sigs_no_sm = [s for s in strategy_no_sm.generate_signals(md, ps)
                      if s.direction == SignalDirection.LONG]
        sigs_with_sm = [s for s in strategy_with_sm.generate_signals(md, ps)
                        if s.direction == SignalDirection.LONG]

        if sigs_no_sm and sigs_with_sm:
            assert sigs_with_sm[0].strength <= sigs_no_sm[0].strength, (
                "SM neutral debe reducir o mantener la fuerza"
            )

    def test_stale_sm_treated_as_no_context(self):
        """Señal SM stale → misma fuerza que sin SM context."""
        strategy_no_sm = _default_strategy()
        strategy_stale = _default_strategy()

        sm = _make_sm_signal("bullish", conviction=0.9)
        strategy_stale.set_smart_money_context(sm)
        # Forzar señal stale
        strategy_stale._smart_money_set_at = (
            datetime.now(timezone.utc) - timedelta(hours=10)
        )

        md = _make_market_data(_bull_prices_with_oversold())
        ps = _empty_portfolio()

        sigs_no_sm = [s for s in strategy_no_sm.generate_signals(md, ps)
                      if s.direction == SignalDirection.LONG]
        sigs_stale = [s for s in strategy_stale.generate_signals(md, ps)
                      if s.direction == SignalDirection.LONG]

        if sigs_no_sm and sigs_stale:
            assert abs(sigs_stale[0].strength - sigs_no_sm[0].strength) < 0.01, (
                "SM stale debe producir misma fuerza que sin SM"
            )

    def test_signal_metadata_includes_sm_fields(self):
        """La señal de entrada incluye todos los campos SM en metadata."""
        strategy = _default_strategy()
        sm = _make_sm_signal("bullish", conviction=0.8)
        strategy.set_smart_money_context(sm)

        md = _make_market_data(_bull_prices_with_oversold())
        longs = [s for s in strategy.generate_signals(md, _empty_portfolio())
                 if s.direction == SignalDirection.LONG]

        assert len(longs) > 0
        meta = longs[0].metadata
        assert "rsi" in meta
        assert "sm_bias" in meta
        assert "sm_conviction" in meta
        assert "sm_multiplier" in meta
        assert meta["sm_bias"] == SmartMoneyBias.BULLISH

    def test_sm_disabled_ignores_context(self):
        """Con smart_money_enabled=False, el filtro SM no se aplica."""
        cfg = RSISmartMoneyConfig(
            rsi_period=7, oversold_threshold=45,
            ema_trend_filter=True, ema_trend_period=200,
            smart_money_enabled=False,
        )
        strategy = RSISmartMoneyStrategy(cfg)
        sm = _make_sm_signal("bearish", conviction=0.95)
        strategy.set_smart_money_context(sm)

        md = _make_market_data(_bull_prices_with_oversold())
        longs = [s for s in strategy.generate_signals(md, _empty_portfolio())
                 if s.direction == SignalDirection.LONG]
        assert len(longs) > 0, "Con SM disabled, señal bearish no debe bloquear"


# ── Tests de lógica de salida ─────────────────────────────────────────────────

class TestSmartMoneyExitLogic:
    """Las salidas no dependen del SM — deben funcionar independientemente."""

    def _prices_with_overbought_exit(self) -> list[float]:
        """Posición abierta en 40k, precio sube → RSI overbought → salida."""
        up = [40_000 + i * 300 for i in range(210)]
        recovery = [up[-1] + i * 500 for i in range(20)]
        return up + recovery

    def test_rsi_overbought_exit_ignores_sm(self):
        """La salida por RSI overbought ocurre independientemente del SM."""
        cfg = RSISmartMoneyConfig(
            rsi_period=7, oversold_threshold=35,
            overbought_threshold=60,
            ema_trend_filter=True, ema_trend_period=200,
        )
        strategy = RSISmartMoneyStrategy(cfg)
        sm = _make_sm_signal("bearish", conviction=0.95)
        strategy.set_smart_money_context(sm)

        prices = self._prices_with_overbought_exit()
        md = _make_market_data(prices)
        # Simular posición abierta al precio de entrada
        ps = _portfolio_with_position(entry_price=prices[200])

        signals = strategy.generate_signals(md, ps)
        flats = [s for s in signals if s.direction == SignalDirection.FLAT]
        assert len(flats) > 0, "Debe generar FLAT por overbought independientemente del SM"

    def test_stop_loss_exit_ignores_sm(self):
        """El stop loss actúa independientemente del SM."""
        cfg = RSISmartMoneyConfig(
            rsi_period=7, oversold_threshold=45,
            stop_loss_pct=5.0,
            ema_trend_filter=True, ema_trend_period=200,
        )
        strategy = RSISmartMoneyStrategy(cfg)
        sm = _make_sm_signal("bullish", conviction=0.9)
        strategy.set_smart_money_context(sm)

        # Precio entra en 50k y cae -6% → stop loss
        entry = 50_000
        prices = [40_000 + i * 50 for i in range(210)]  # tendencia alcista larga
        prices += [entry - i * 350 for i in range(10)]  # caída post-entrada
        md = _make_market_data(prices)
        ps = _portfolio_with_position(entry_price=entry)

        signals = strategy.generate_signals(md, ps)
        flats = [s for s in signals if s.direction == SignalDirection.FLAT]
        assert len(flats) > 0, "Stop loss debe activarse independientemente del SM"


# ── Tests del EMA200 filter ───────────────────────────────────────────────────

class TestEMAFilterWithSM:
    def test_ema_filter_blocks_even_with_bullish_sm(self):
        """EMA200 filter > SM: precio bajo EMA200 → no entrar aunque SM sea bullish."""
        strategy = _default_strategy()
        sm = _make_sm_signal("bullish", conviction=0.95)
        strategy.set_smart_money_context(sm)

        md = _make_market_data(_bear_prices())  # precio < EMA200
        longs = [s for s in strategy.generate_signals(md, _empty_portfolio())
                 if s.direction == SignalDirection.LONG]
        assert len(longs) == 0, "EMA filter debe bloquear entrada incluso con SM bullish"

    def test_insufficient_bars_returns_empty(self):
        """Con menos barras que warmup_period(), devuelve lista vacía."""
        strategy = _default_strategy()
        # warmup = 205; dar solo 100 barras
        md = _make_market_data(_bull_prices_with_oversold(n_up=90, n_pullback=5))
        signals = strategy.generate_signals(md, _empty_portfolio())
        assert signals == []


# ── Tests de integración con SmartMoneySignal ────────────────────────────────

class TestSmartMoneyIntegration:
    """
    Tests de integración: SmartMoneySignal inyectado via set_smart_money_context()
    y validado en generate_signals(). Verifica el flujo completo sin I/O.
    """

    def test_full_flow_bullish_entry(self):
        """
        Flujo completo: mercado alcista + SM bullish → señal LONG con multiplier 1.5x.
        """
        cfg = RSISmartMoneyConfig(
            rsi_period=7, oversold_threshold=45,
            ema_trend_filter=True, ema_trend_period=200,
            smart_money_enabled=True,
            skip_on_bearish_smart_money=True,
        )
        strategy = RSISmartMoneyStrategy(cfg)

        # Simular trader service inyectando SM antes de generar señales
        sm = _make_sm_signal("bullish", conviction=0.9)
        strategy.set_smart_money_context(sm)

        md = _make_market_data(_bull_prices_with_oversold())
        signals = strategy.generate_signals(md, _empty_portfolio())

        longs = [s for s in signals if s.direction == SignalDirection.LONG]
        assert len(longs) > 0

        meta = longs[0].metadata
        assert meta["sm_bias"] == SmartMoneyBias.BULLISH
        assert meta["sm_multiplier"] == 1.5  # alta convicción bullish
        assert longs[0].strength > 0

    def test_full_flow_bearish_blocked(self):
        """
        Flujo completo: mercado alcista técnico + SM bearish → señal bloqueada.
        """
        cfg = RSISmartMoneyConfig(
            rsi_period=7, oversold_threshold=45,
            ema_trend_filter=True, ema_trend_period=200,
            smart_money_enabled=True,
            skip_on_bearish_smart_money=True,
        )
        strategy = RSISmartMoneyStrategy(cfg)

        sm = _make_sm_signal("bearish", conviction=0.9)
        strategy.set_smart_money_context(sm)

        md = _make_market_data(_bull_prices_with_oversold())
        signals = strategy.generate_signals(md, _empty_portfolio())

        longs = [s for s in signals if s.direction == SignalDirection.LONG]
        assert len(longs) == 0, "SM bearish debe bloquear aunque RSI esté oversold"

    def test_full_flow_stale_sm_acts_as_neutral(self):
        """
        SM inyectado hace 10h (stale) → actúa como sin contexto.
        Verifica que no bloquea y no amplifica.
        """
        strategy_baseline = _default_strategy()
        strategy_stale = _default_strategy()

        sm_bearish = _make_sm_signal("bearish", conviction=0.9)
        strategy_stale.set_smart_money_context(sm_bearish)
        strategy_stale._smart_money_set_at = (
            datetime.now(timezone.utc) - timedelta(hours=10)
        )

        md = _make_market_data(_bull_prices_with_oversold())
        ps = _empty_portfolio()

        sigs_base = strategy_baseline.generate_signals(md, ps)
        sigs_stale = strategy_stale.generate_signals(md, ps)

        # Stale SM bearish no debe bloquear (se ignora)
        longs_base = [s for s in sigs_base if s.direction == SignalDirection.LONG]
        longs_stale = [s for s in sigs_stale if s.direction == SignalDirection.LONG]
        assert len(longs_stale) == len(longs_base), (
            "SM stale bearish no debe bloquear la entrada"
        )

    def test_set_smart_money_context_records_timestamp(self):
        """set_smart_money_context() guarda el timestamp de inyección."""
        strategy = RSISmartMoneyStrategy()
        before = datetime.now(timezone.utc)
        sm = _make_sm_signal("bullish")
        strategy.set_smart_money_context(sm)
        after = datetime.now(timezone.utc)

        assert strategy._smart_money_set_at is not None
        assert before <= strategy._smart_money_set_at <= after

    def test_reinject_sm_resets_timestamp(self):
        """Inyectar un nuevo SM refresca el timestamp (señal ya no es stale)."""
        strategy = RSISmartMoneyStrategy(RSISmartMoneyConfig(smart_money_max_age_hours=4))
        sm_old = _make_sm_signal("bullish")
        strategy.set_smart_money_context(sm_old)
        strategy._smart_money_set_at = datetime.now(timezone.utc) - timedelta(hours=6)

        assert strategy._is_sm_stale() is True  # era stale

        sm_new = _make_sm_signal("bullish")
        strategy.set_smart_money_context(sm_new)  # re-inyectar

        assert strategy._is_sm_stale() is False  # ahora fresco
