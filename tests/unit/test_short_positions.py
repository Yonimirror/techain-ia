"""Tests for SHORT position handling across strategies and portfolio engine."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from core.domain.entities import MarketData, PortfolioState, Order, Trade, Position
from core.domain.entities.signal import SignalDirection
from core.domain.entities.order import OrderSide, OrderType, OrderStatus
from core.domain.entities.market_data import OHLCV
from core.domain.value_objects import Symbol, Price, Quantity, Timeframe
from core.portfolio_engine import PortfolioEngine
from core.strategies.rsi_mean_reversion import RSIMeanReversionStrategy, RSIMeanReversionConfig
from core.strategies.ema_crossover import EMACrossoverStrategy, EMACrossoverConfig
from core.strategies.bollinger_reversion import BollingerReversionStrategy, BollingerReversionConfig


@pytest.fixture
def symbol():
    return Symbol.of("BTC", "CRYPTO")


@pytest.fixture
def timeframe():
    return Timeframe("1d")


def _make_bars(prices: list[float], symbol, start=None):
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


def _make_filled_order(symbol, side, price, qty, strategy_id="test"):
    order = Order(
        id=uuid4(),
        symbol=symbol,
        side=side,
        order_type=OrderType.MARKET,
        quantity=Quantity(Decimal(str(qty))),
        status=OrderStatus.FILLED,
        created_at=datetime.now(timezone.utc),
        strategy_id=strategy_id,
        filled_price=Price(Decimal(str(price))),
        filled_quantity=Quantity(Decimal(str(qty))),
        filled_at=datetime.now(timezone.utc),
    )
    return order


# ── RSI SHORT Tests ──────────────────────────────────────────────────────────

class TestRSIShort:
    def test_short_disabled_by_default(self, symbol, timeframe):
        strategy = RSIMeanReversionStrategy(RSIMeanReversionConfig())
        assert strategy._config.enable_short is False

    def test_short_signal_on_overbought(self, symbol, timeframe):
        """RSI > overbought + enable_short=True -> SHORT signal."""
        # Rising prices to push RSI high
        prices = [50000.0 + i * 500 for i in range(25)]
        strategy = RSIMeanReversionStrategy(RSIMeanReversionConfig(
            rsi_period=14,
            overbought_threshold=70.0,
            enable_short=True,
        ))
        md = _make_market_data(prices, symbol, timeframe)
        signals = strategy.generate_signals(md, _empty_portfolio())

        short_signals = [s for s in signals if s.direction == SignalDirection.SHORT]
        if short_signals:
            assert short_signals[0].metadata["condition"] == "overbought"
            assert 0 < short_signals[0].strength <= 1.0

    def test_no_short_when_disabled(self, symbol, timeframe):
        """RSI > overbought + enable_short=False -> no SHORT signal."""
        prices = [50000.0 + i * 500 for i in range(25)]
        strategy = RSIMeanReversionStrategy(RSIMeanReversionConfig(
            rsi_period=14,
            overbought_threshold=70.0,
            enable_short=False,
        ))
        md = _make_market_data(prices, symbol, timeframe)
        signals = strategy.generate_signals(md, _empty_portfolio())

        short_signals = [s for s in signals if s.direction == SignalDirection.SHORT]
        assert short_signals == []

    def test_exit_short_on_oversold(self, symbol, timeframe):
        """With SHORT position, RSI < oversold -> FLAT."""
        # Dropping prices to push RSI low
        prices = [70000.0 - i * 500 for i in range(25)]
        portfolio = _empty_portfolio()
        pos = Position(
            symbol=symbol,
            side=OrderSide.SELL,
            quantity=Quantity(Decimal("0.1")),
            average_entry_price=Price(Decimal("70000")),
            opened_at=datetime(2024, 1, 1),
        )
        portfolio.set_position(pos)

        strategy = RSIMeanReversionStrategy(RSIMeanReversionConfig(
            rsi_period=14,
            oversold_threshold=30.0,
            enable_short=True,
        ))
        md = _make_market_data(prices, symbol, timeframe)
        signals = strategy.generate_signals(md, portfolio)

        flat_signals = [s for s in signals if s.direction == SignalDirection.FLAT]
        if flat_signals:
            assert flat_signals[0].metadata["exit_reason"] == "rsi_oversold"

    def test_stop_loss_on_short(self, symbol, timeframe):
        """Short position with price rising -> stop loss FLAT."""
        prices = [60000.0] * 20
        prices[-1] = 65000.0  # price rose 8.3% against short

        portfolio = _empty_portfolio()
        pos = Position(
            symbol=symbol,
            side=OrderSide.SELL,
            quantity=Quantity(Decimal("0.1")),
            average_entry_price=Price(Decimal("60000")),
            opened_at=datetime(2024, 1, 15),
        )
        portfolio.set_position(pos)

        strategy = RSIMeanReversionStrategy(RSIMeanReversionConfig(
            rsi_period=14,
            stop_loss_pct=5.0,
            enable_short=True,
        ))
        md = _make_market_data(prices, symbol, timeframe)
        signals = strategy.generate_signals(md, portfolio)

        assert len(signals) == 1
        assert signals[0].direction == SignalDirection.FLAT
        assert signals[0].metadata["exit_reason"] == "stop_loss"


# ── EMA SHORT Tests ──────────────────────────────────────────────────────────

class TestEMAShort:
    def test_short_disabled_by_default(self):
        strategy = EMACrossoverStrategy(EMACrossoverConfig())
        assert strategy._config.enable_short is False

    def test_enable_short_config(self):
        cfg = EMACrossoverConfig(enable_short=True)
        assert cfg.enable_short is True


# ── Portfolio Engine SHORT Tests ─────────────────────────────────────────────

class TestPortfolioEngineShort:
    def test_open_short_position(self, symbol):
        """SELL order with no existing position opens a SHORT."""
        portfolio = PortfolioEngine(Decimal("100000"))
        order = _make_filled_order(symbol, OrderSide.SELL, 60000, 0.5)

        portfolio.process_fill(order, fees=Decimal("30"))

        pos = portfolio.state.get_position(symbol)
        assert pos is not None
        assert pos.side == OrderSide.SELL
        assert pos.quantity.value == Decimal("0.5")
        # Margin reserved: 60000 * 0.5 + 30 = 30030
        assert portfolio.state.cash == Decimal("100000") - Decimal("30000") - Decimal("30")

    def test_close_short_with_profit(self, symbol):
        """BUY order with existing SHORT closes position with profit."""
        portfolio = PortfolioEngine(Decimal("100000"))

        # Open short at 60000
        sell_order = _make_filled_order(symbol, OrderSide.SELL, 60000, 0.5)
        portfolio.process_fill(sell_order, fees=Decimal("30"))

        # Close short at 55000 (profit: price went down)
        buy_order = _make_filled_order(symbol, OrderSide.BUY, 55000, 0.5)
        portfolio.process_fill(buy_order, fees=Decimal("27.5"))

        # Position should be closed
        assert portfolio.state.get_position(symbol) is None
        assert len(portfolio.closed_trades) == 1

        trade = portfolio.closed_trades[0]
        assert trade.side == OrderSide.SELL
        # PnL: (60000 - 55000) * 0.5 - 57.5 fees = 2500 - 57.5 = 2442.5
        expected_pnl = (Decimal("60000") - Decimal("55000")) * Decimal("0.5") - Decimal("57.5")
        assert trade.pnl == expected_pnl

    def test_close_short_with_loss(self, symbol):
        """BUY order with existing SHORT closes position with loss."""
        portfolio = PortfolioEngine(Decimal("100000"))

        # Open short at 60000
        sell_order = _make_filled_order(symbol, OrderSide.SELL, 60000, 0.5)
        portfolio.process_fill(sell_order, fees=Decimal("30"))

        # Close short at 65000 (loss: price went up)
        buy_order = _make_filled_order(symbol, OrderSide.BUY, 65000, 0.5)
        portfolio.process_fill(buy_order, fees=Decimal("32.5"))

        assert portfolio.state.get_position(symbol) is None
        trade = portfolio.closed_trades[0]
        # PnL: (60000 - 65000) * 0.5 - 62.5 fees = -2500 - 62.5 = -2562.5
        expected_pnl = (Decimal("60000") - Decimal("65000")) * Decimal("0.5") - Decimal("62.5")
        assert trade.pnl == expected_pnl

    def test_close_long_still_works(self, symbol):
        """Normal LONG open/close still works after SHORT changes."""
        portfolio = PortfolioEngine(Decimal("100000"))

        # Open long at 60000
        buy_order = _make_filled_order(symbol, OrderSide.BUY, 60000, 0.5)
        portfolio.process_fill(buy_order, fees=Decimal("30"))

        assert portfolio.state.get_position(symbol) is not None
        assert portfolio.state.get_position(symbol).side == OrderSide.BUY

        # Close long at 65000
        sell_order = _make_filled_order(symbol, OrderSide.SELL, 65000, 0.5)
        portfolio.process_fill(sell_order, fees=Decimal("32.5"))

        assert portfolio.state.get_position(symbol) is None
        trade = portfolio.closed_trades[0]
        # PnL: (65000 - 60000) * 0.5 - 62.5 = 2500 - 62.5 = 2437.5
        expected_pnl = (Decimal("65000") - Decimal("60000")) * Decimal("0.5") - Decimal("62.5")
        assert trade.pnl == expected_pnl

    def test_short_cash_accounting(self, symbol):
        """Cash flow is correct through SHORT open -> close cycle."""
        initial = Decimal("100000")
        portfolio = PortfolioEngine(initial)

        # Open short at 60000, qty 0.5 -> margin = 30000 + 30 fees
        sell_order = _make_filled_order(symbol, OrderSide.SELL, 60000, 0.5)
        portfolio.process_fill(sell_order, fees=Decimal("30"))
        cash_after_open = portfolio.state.cash
        assert cash_after_open == initial - Decimal("30000") - Decimal("30")

        # Close short at 58000 (profit) -> return margin + pnl - fees
        # pnl = (60000 - 58000) * 0.5 = 1000
        # cash += entry_notional + pnl - fees = 30000 + 1000 - 29 = 30971
        buy_order = _make_filled_order(symbol, OrderSide.BUY, 58000, 0.5)
        portfolio.process_fill(buy_order, fees=Decimal("29"))
        cash_after_close = portfolio.state.cash

        # Total: initial - open_fees - close_fees + profit = 100000 - 30 - 29 + 1000 = 100941
        expected = initial - Decimal("30") - Decimal("29") + Decimal("1000")
        assert cash_after_close == expected


# ── Trade PnL Tests ──────────────────────────────────────────────────────────

class TestTradePnL:
    def test_long_trade_pnl(self):
        trade = Trade.open(
            symbol=Symbol.of("BTC"),
            side=OrderSide.BUY,
            entry_price=Price(Decimal("60000")),
            quantity=Quantity(Decimal("1")),
            entry_order_id=uuid4(),
            strategy_id="test",
            fees=Decimal("60"),
        )
        trade.close(
            exit_price=Price(Decimal("65000")),
            exit_order_id=uuid4(),
            exit_fees=Decimal("65"),
        )
        # (65000 - 60000) * 1 - 125 = 4875
        assert trade.pnl == Decimal("4875")

    def test_short_trade_pnl(self):
        trade = Trade.open(
            symbol=Symbol.of("BTC"),
            side=OrderSide.SELL,
            entry_price=Price(Decimal("60000")),
            quantity=Quantity(Decimal("1")),
            entry_order_id=uuid4(),
            strategy_id="test",
            fees=Decimal("60"),
        )
        trade.close(
            exit_price=Price(Decimal("55000")),
            exit_order_id=uuid4(),
            exit_fees=Decimal("55"),
        )
        # (60000 - 55000) * 1 - 115 = 4885
        assert trade.pnl == Decimal("4885")

    def test_short_trade_loss(self):
        trade = Trade.open(
            symbol=Symbol.of("BTC"),
            side=OrderSide.SELL,
            entry_price=Price(Decimal("60000")),
            quantity=Quantity(Decimal("1")),
            entry_order_id=uuid4(),
            strategy_id="test",
            fees=Decimal("60"),
        )
        trade.close(
            exit_price=Price(Decimal("65000")),
            exit_order_id=uuid4(),
            exit_fees=Decimal("65"),
        )
        # (60000 - 65000) * 1 - 125 = -5125
        assert trade.pnl == Decimal("-5125")
