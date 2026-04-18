"""Unit tests for domain value objects and entities."""
import pytest
from decimal import Decimal
from datetime import datetime

from core.domain.value_objects import Price, Quantity, Symbol, Timeframe
from core.domain.entities import Signal, Order, Trade
from core.domain.entities.signal import SignalDirection
from core.domain.entities.order import OrderSide, OrderType, OrderStatus


class TestPrice:
    def test_immutable(self):
        p = Price.of(100)
        with pytest.raises(Exception):
            p.value = Decimal("200")  # type: ignore

    def test_negative_raises(self):
        with pytest.raises(ValueError):
            Price(Decimal("-1"))

    def test_arithmetic(self):
        p1 = Price.of(100)
        p2 = Price.of(50)
        assert (p1 + p2).value == Decimal("150")
        assert (p1 - p2).value == Decimal("50")
        assert (p1 * 2).value == Decimal("200")

    def test_comparison(self):
        assert Price.of(100) > Price.of(50)
        assert Price.of(50) < Price.of(100)
        assert Price.of(100) == Price.of(100)


class TestQuantity:
    def test_negative_raises(self):
        with pytest.raises(ValueError):
            Quantity(Decimal("-1"))

    def test_zero_allowed(self):
        q = Quantity.of(0)
        assert q.value == Decimal("0")

    def test_arithmetic(self):
        q1 = Quantity.of(10)
        q2 = Quantity.of(5)
        assert (q1 + q2).value == Decimal("15")
        assert (q1 - q2).value == Decimal("5")
        assert (q1 * 2).value == Decimal("20")


class TestSymbol:
    def test_uppercases_ticker(self):
        s = Symbol.of("aapl")
        assert s.ticker == "AAPL"

    def test_empty_ticker_raises(self):
        with pytest.raises(ValueError):
            Symbol.of("")

    def test_str(self):
        s = Symbol.of("AAPL", "NASDAQ")
        assert str(s) == "AAPL:NASDAQ"


class TestSignal:
    def test_strength_bounds(self):
        with pytest.raises(ValueError):
            Signal.create(
                strategy_id="test",
                symbol=Symbol.of("AAPL"),
                direction=SignalDirection.LONG,
                strength=1.5,  # invalid
                price=Price.of(100),
                timeframe=Timeframe.D1,
            )

    def test_is_entry(self):
        s = Signal.create(
            strategy_id="test", symbol=Symbol.of("AAPL"),
            direction=SignalDirection.LONG, strength=0.5,
            price=Price.of(100), timeframe=Timeframe.D1,
        )
        assert s.is_entry
        assert not s.is_exit

    def test_is_exit(self):
        s = Signal.create(
            strategy_id="test", symbol=Symbol.of("AAPL"),
            direction=SignalDirection.FLAT, strength=0.5,
            price=Price.of(100), timeframe=Timeframe.D1,
        )
        assert s.is_exit
        assert not s.is_entry


class TestOrder:
    def test_market_order_creation(self):
        order = Order.create_market(
            symbol=Symbol.of("AAPL"),
            side=OrderSide.BUY,
            quantity=Quantity.of(10),
        )
        assert order.order_type == OrderType.MARKET
        assert order.status == OrderStatus.PENDING
        assert order.is_active

    def test_fill(self):
        order = Order.create_market(
            symbol=Symbol.of("AAPL"),
            side=OrderSide.BUY,
            quantity=Quantity.of(10),
        )
        order.fill(Price.of(150), Quantity.of(10))
        assert order.is_filled
        assert order.filled_price == Price.of(150)


class TestTrade:
    def test_pnl_long(self):
        order = Order.create_market(
            symbol=Symbol.of("AAPL"),
            side=OrderSide.BUY,
            quantity=Quantity.of(10),
        )
        trade = Trade.open(
            symbol=Symbol.of("AAPL"),
            side=OrderSide.BUY,
            entry_price=Price.of(100),
            quantity=Quantity.of(10),
            entry_order_id=order.id,
            strategy_id="test",
        )
        trade.close(
            exit_price=Price.of(110),
            exit_order_id=order.id,
        )
        assert trade.pnl == Decimal("100")   # 10 shares * $10 gain
        assert float(trade.pnl_pct) == pytest.approx(10.0)

    def test_pnl_short(self):
        order = Order.create_market(
            symbol=Symbol.of("AAPL"),
            side=OrderSide.SELL,
            quantity=Quantity.of(10),
        )
        trade = Trade.open(
            symbol=Symbol.of("AAPL"),
            side=OrderSide.SELL,
            entry_price=Price.of(100),
            quantity=Quantity.of(10),
            entry_order_id=order.id,
            strategy_id="test",
        )
        trade.close(exit_price=Price.of(90), exit_order_id=order.id)
        assert trade.pnl == Decimal("100")  # 10 shares * $10 gain (short)
