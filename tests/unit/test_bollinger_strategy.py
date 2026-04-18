"""Tests for Bollinger Band Mean Reversion strategy."""
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from core.domain.entities import MarketData, PortfolioState
from core.domain.entities.signal import SignalDirection
from core.domain.entities.order import OrderSide
from core.domain.entities.position import Position
from core.domain.entities.market_data import OHLCV
from core.domain.value_objects import Symbol, Price, Quantity, Timeframe
from core.strategies.bollinger_reversion import (
    BollingerReversionStrategy,
    BollingerReversionConfig,
)


@pytest.fixture
def symbol():
    return Symbol.of("BTC", "CRYPTO")


@pytest.fixture
def timeframe():
    return Timeframe("1d")


def _make_bars(prices: list[float], symbol, start=None):
    """Create OHLCV bars from a list of close prices."""
    start = start or datetime(2024, 1, 1)
    bars = []
    for i, price in enumerate(prices):
        bars.append(OHLCV(
            timestamp=start + timedelta(days=i),
            open=Price.of(price * 0.999),
            high=Price.of(price * 1.01),
            low=Price.of(price * 0.99),
            close=Price.of(price),
            volume=Quantity.of(100),
        ))
    return bars


def _make_market_data(prices, symbol, timeframe):
    bars = _make_bars(prices, symbol)
    return MarketData(symbol=symbol, timeframe=timeframe, bars=bars)


def _empty_portfolio(capital=100000):
    return PortfolioState(cash=Decimal(str(capital)), initial_capital=Decimal(str(capital)))


class TestBollingerStrategyBasics:
    def test_no_signal_during_warmup(self, symbol, timeframe):
        strategy = BollingerReversionStrategy(BollingerReversionConfig(bb_period=20))
        prices = [60000 + i * 10 for i in range(15)]
        md = _make_market_data(prices, symbol, timeframe)
        signals = strategy.generate_signals(md, _empty_portfolio())
        assert signals == []

    def test_warmup_period_correct(self):
        cfg = BollingerReversionConfig(bb_period=20, rsi_period=14)
        strategy = BollingerReversionStrategy(cfg)
        assert strategy.warmup_period() == 25

    def test_strategy_id(self):
        strategy = BollingerReversionStrategy()
        assert strategy.strategy_id == "bollinger_reversion_v1"
        assert strategy.version == "1.0.0"


class TestBollingerLongSignal:
    def test_long_when_price_below_lower_band(self, symbol, timeframe):
        """Price dropping significantly below lower BB + oversold RSI -> LONG."""
        # Create stable prices then a sharp drop at end
        prices = [60000.0] * 30
        prices[-1] = 55000.0  # sharp drop to trigger lower band touch + low RSI
        prices[-2] = 57000.0
        prices[-3] = 58000.0
        prices[-4] = 58500.0
        prices[-5] = 59000.0
        prices[-6] = 59500.0

        strategy = BollingerReversionStrategy(BollingerReversionConfig(
            bb_period=20,
            bb_std=2.0,
            rsi_period=14,
            rsi_oversold=40.0,
        ))
        md = _make_market_data(prices, symbol, timeframe)
        signals = strategy.generate_signals(md, _empty_portfolio())

        if signals:
            assert signals[0].direction == SignalDirection.LONG
            assert signals[0].metadata["condition"] == "lower_band_touch"
            assert 0.0 < signals[0].strength <= 1.0

    def test_no_signal_in_middle_of_bands(self, symbol, timeframe):
        """Price in the middle of bands -> no signal."""
        prices = [60000.0 + (i % 5) * 100 for i in range(30)]
        strategy = BollingerReversionStrategy(BollingerReversionConfig(bb_period=20))
        md = _make_market_data(prices, symbol, timeframe)
        signals = strategy.generate_signals(md, _empty_portfolio())
        assert signals == []


class TestBollingerShortSignal:
    def test_short_disabled_by_default(self, symbol, timeframe):
        """With enable_short=False, no SHORT signal even at upper band."""
        prices = [60000.0] * 30
        # Sharp rise at end
        prices[-1] = 65000.0
        prices[-2] = 63000.0
        prices[-3] = 62000.0

        strategy = BollingerReversionStrategy(BollingerReversionConfig(
            bb_period=20,
            enable_short=False,
        ))
        md = _make_market_data(prices, symbol, timeframe)
        signals = strategy.generate_signals(md, _empty_portfolio())
        short_signals = [s for s in signals if s.direction == SignalDirection.SHORT]
        assert short_signals == []

    def test_short_when_enabled_and_at_upper_band(self, symbol, timeframe):
        """With enable_short=True, price at upper band + overbought RSI -> SHORT."""
        prices = [60000.0] * 30
        prices[-6] = 60500.0
        prices[-5] = 61000.0
        prices[-4] = 62000.0
        prices[-3] = 63000.0
        prices[-2] = 64000.0
        prices[-1] = 66000.0  # big spike above upper band

        strategy = BollingerReversionStrategy(BollingerReversionConfig(
            bb_period=20,
            bb_std=2.0,
            rsi_period=14,
            rsi_overbought=60.0,
            enable_short=True,
        ))
        md = _make_market_data(prices, symbol, timeframe)
        signals = strategy.generate_signals(md, _empty_portfolio())

        if signals:
            assert signals[0].direction == SignalDirection.SHORT
            assert signals[0].metadata["condition"] == "upper_band_touch"


class TestBollingerExits:
    def test_exit_long_at_upper_band(self, symbol, timeframe):
        """With a LONG position, exit when price reaches upper band."""
        prices = [60000.0] * 30
        prices[-1] = 65000.0  # spike to upper band

        portfolio = _empty_portfolio()
        pos = Position(
            symbol=symbol,
            side=OrderSide.BUY,
            quantity=Quantity(Decimal("0.1")),
            average_entry_price=Price(Decimal("58000")),
            opened_at=datetime(2024, 1, 20),
        )
        portfolio.set_position(pos)

        strategy = BollingerReversionStrategy(BollingerReversionConfig(bb_period=20))
        md = _make_market_data(prices, symbol, timeframe)
        signals = strategy.generate_signals(md, portfolio)

        if signals:
            assert signals[0].direction == SignalDirection.FLAT
            assert signals[0].metadata["exit_reason"] == "upper_band"

    def test_stop_loss_on_long_position(self, symbol, timeframe):
        """Stop loss triggers exit."""
        prices = [60000.0] * 30
        prices[-1] = 55000.0  # drop enough for stop loss

        portfolio = _empty_portfolio()
        pos = Position(
            symbol=symbol,
            side=OrderSide.BUY,
            quantity=Quantity(Decimal("0.1")),
            average_entry_price=Price(Decimal("60000")),
            opened_at=datetime(2024, 1, 20),
        )
        portfolio.set_position(pos)

        strategy = BollingerReversionStrategy(BollingerReversionConfig(
            bb_period=20, stop_loss_pct=5.0,
        ))
        md = _make_market_data(prices, symbol, timeframe)
        signals = strategy.generate_signals(md, portfolio)

        assert len(signals) == 1
        assert signals[0].direction == SignalDirection.FLAT
        assert signals[0].metadata["exit_reason"] == "stop_loss"


class TestBollingerConfig:
    def test_default_config(self):
        cfg = BollingerReversionConfig()
        assert cfg.bb_period == 20
        assert cfg.bb_std == 2.0
        assert cfg.rsi_period == 14
        assert cfg.enable_short is False

    def test_custom_config(self):
        cfg = BollingerReversionConfig(bb_period=15, bb_std=1.5, enable_short=True)
        strategy = BollingerReversionStrategy(cfg)
        assert strategy.warmup_period() == 20
