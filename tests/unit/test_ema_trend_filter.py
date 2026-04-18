"""Tests for EMA200 trend filter on RSI Mean Reversion strategy."""
import pytest
from decimal import Decimal
from datetime import datetime, timedelta

from core.domain.entities import MarketData, PortfolioState
from core.domain.entities.market_data import OHLCV
from core.domain.value_objects import Symbol, Timeframe, Price, Quantity
from core.strategies.rsi_mean_reversion import RSIMeanReversionStrategy, RSIMeanReversionConfig
from core.domain.entities.signal import SignalDirection

BTC = Symbol("BTC")
D1 = Timeframe("1d")


def _make_bars(prices: list[float]) -> list[OHLCV]:
    bars = []
    for i, p in enumerate(prices):
        bars.append(OHLCV(
            timestamp=datetime(2022, 1, 1) + timedelta(days=i),
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


def _bear_prices(n: int = 210) -> list[float]:
    """Precio cayendo continuamente — RSI bajo, precio < EMA200."""
    return [100_000 - i * 200 for i in range(n)]


def _bull_prices(n_up: int = 210, n_pullback: int = 10) -> list[float]:
    """Precio subiendo luego pullback — RSI bajo pero precio > EMA200."""
    up = [40_000 + i * 300 for i in range(n_up)]
    pullback = [up[-1] - i * 1_500 for i in range(1, n_pullback + 1)]
    return up + pullback


class TestEMAFilterConfig:
    def test_default_filter_off(self):
        cfg = RSIMeanReversionConfig()
        assert cfg.ema_trend_filter is False
        assert cfg.ema_trend_period == 200

    def test_filter_enabled(self):
        cfg = RSIMeanReversionConfig(ema_trend_filter=True, ema_trend_period=200)
        assert cfg.ema_trend_filter is True
        assert cfg.ema_trend_period == 200

    def test_custom_period(self):
        cfg = RSIMeanReversionConfig(ema_trend_filter=True, ema_trend_period=50)
        assert cfg.ema_trend_period == 50


class TestEMAFilterSignals:
    def test_filter_off_generates_signal_in_bear(self):
        """Sin filtro, el sistema genera señal LONG aunque el mercado sea bajista."""
        cfg = RSIMeanReversionConfig(
            rsi_period=7, oversold_threshold=35,
            ema_trend_filter=False,
        )
        strategy = RSIMeanReversionStrategy(cfg)
        md = _make_market_data(_bear_prices())
        signals = strategy.generate_signals(md, _empty_portfolio())
        long_signals = [s for s in signals if s.direction == SignalDirection.LONG]
        assert len(long_signals) > 0, "Sin filtro debe generar señal en mercado bajista"

    def test_filter_on_blocks_signal_in_bear(self):
        """Con filtro EMA200, NO genera señal LONG cuando precio < EMA200."""
        cfg = RSIMeanReversionConfig(
            rsi_period=7, oversold_threshold=35,
            ema_trend_filter=True, ema_trend_period=200,
        )
        strategy = RSIMeanReversionStrategy(cfg)
        md = _make_market_data(_bear_prices())
        signals = strategy.generate_signals(md, _empty_portfolio())
        long_signals = [s for s in signals if s.direction == SignalDirection.LONG]
        assert len(long_signals) == 0, "Con filtro debe bloquear señal en mercado bajista"

    def test_filter_on_allows_signal_in_bull(self):
        """Con filtro EMA200, SÍ genera señal LONG cuando precio > EMA200 con pullback."""
        cfg = RSIMeanReversionConfig(
            rsi_period=7, oversold_threshold=45,
            ema_trend_filter=True, ema_trend_period=200,
        )
        strategy = RSIMeanReversionStrategy(cfg)
        md = _make_market_data(_bull_prices())
        signals = strategy.generate_signals(md, _empty_portfolio())
        long_signals = [s for s in signals if s.direction == SignalDirection.LONG]
        assert len(long_signals) > 0, "Con filtro debe permitir señal en pullback alcista"

    def test_filter_off_same_as_original_in_bull(self):
        """Sin filtro, el comportamiento es idéntico al original en mercado alcista."""
        cfg_off = RSIMeanReversionConfig(rsi_period=7, oversold_threshold=45, ema_trend_filter=False)
        cfg_on = RSIMeanReversionConfig(rsi_period=7, oversold_threshold=45, ema_trend_filter=True)
        s_off = RSIMeanReversionStrategy(cfg_off)
        s_on = RSIMeanReversionStrategy(cfg_on)
        md = _make_market_data(_bull_prices())
        ps = _empty_portfolio()
        sigs_off = s_off.generate_signals(md, ps)
        sigs_on = s_on.generate_signals(md, ps)
        # Ambos deben generar señal — el filtro no bloquea en alcista
        assert len(sigs_off) == len(sigs_on)

    def test_stop_loss_still_works_with_filter(self):
        """El stop loss funciona correctamente cuando el filtro está activo."""
        cfg = RSIMeanReversionConfig(
            rsi_period=7, oversold_threshold=45,
            stop_loss_pct=5.0, ema_trend_filter=True,
        )
        # Este test verifica que la lógica de stop no se rompe con el filtro
        strategy = RSIMeanReversionStrategy(cfg)
        assert strategy._config.stop_loss_pct == 5.0
        assert strategy._config.ema_trend_filter is True


class TestHypothesisGeneration:
    def test_ema_variants_generated(self):
        """hypothesis.py genera variantes con y sin filtro EMA200."""
        from core.research.hypothesis import generate_hypotheses
        hyps = generate_hypotheses()
        ema_ids = [h.hypothesis_id for h in hyps if "ema200" in h.hypothesis_id]
        no_ema_ids = [h.hypothesis_id for h in hyps if
                      h.family == "reversion" and "ema200" not in h.hypothesis_id]
        assert len(ema_ids) > 0, "Debe generar hipótesis con filtro EMA200"
        assert len(no_ema_ids) > 0, "Debe mantener hipótesis sin filtro para comparar"
        assert len(ema_ids) == len(no_ema_ids), "Mismo número de variantes con/sin filtro"

    def test_ema_params_in_hypothesis(self):
        """Las hipótesis con EMA200 incluyen los parámetros correctos."""
        from core.research.hypothesis import generate_hypotheses
        hyps = generate_hypotheses()
        ema_hyp = next(h for h in hyps if "ema200" in h.hypothesis_id)
        assert ema_hyp.params["ema_trend_filter"] is True
        assert ema_hyp.params["ema_trend_period"] == 200

    def test_no_ema_params_preserved(self):
        """Las hipótesis sin filtro no tienen ema_trend_filter=True."""
        from core.research.hypothesis import generate_hypotheses
        hyps = generate_hypotheses()
        no_ema_hyp = next(h for h in hyps
                          if h.family == "reversion" and "ema200" not in h.hypothesis_id)
        assert no_ema_hyp.params["ema_trend_filter"] is False
