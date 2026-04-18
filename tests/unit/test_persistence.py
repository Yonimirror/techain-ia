"""Tests for paper trading state persistence."""
from datetime import datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from core.domain.entities import Trade, Position
from core.domain.entities.order import OrderSide
from core.domain.entities.trade import TradeStatus
from core.domain.value_objects import Symbol, Price, Quantity
from core.portfolio_engine import PortfolioEngine
from core.portfolio_engine.persistence import (
    save_state,
    load_state,
    delete_state,
    rebuild_portfolio,
    restore_risk_state,
    _serialize_trade,
    _deserialize_trade,
    _serialize_position,
    _deserialize_position,
    STATE_DIR,
)
from core.risk_engine import RiskEngine, RiskConfig


@pytest.fixture
def symbol():
    return Symbol.of("BTC", "CRYPTO")


@pytest.fixture
def portfolio_with_position(symbol):
    portfolio = PortfolioEngine(Decimal("100000"))
    pos = Position(
        symbol=symbol,
        side=OrderSide.BUY,
        quantity=Quantity(Decimal("0.5")),
        average_entry_price=Price(Decimal("60000")),
        opened_at=datetime(2026, 3, 1, 12, 0, 0),
        trade_ids=[uuid4()],
    )
    portfolio._state.set_position(pos)
    portfolio._state.cash = Decimal("70000")
    portfolio._state.peak_equity = Decimal("105000")

    trade = Trade(
        id=uuid4(),
        symbol=symbol,
        side=OrderSide.BUY,
        entry_price=Price(Decimal("60000")),
        quantity=Quantity(Decimal("0.5")),
        entry_order_id=uuid4(),
        strategy_id="test_strategy",
        status=TradeStatus.OPEN,
        opened_at=datetime(2026, 3, 1, 12, 0, 0),
        fees=Decimal("30"),
    )
    portfolio._open_trades[str(symbol)] = trade

    closed_trade = Trade(
        id=uuid4(),
        symbol=symbol,
        side=OrderSide.BUY,
        entry_price=Price(Decimal("55000")),
        quantity=Quantity(Decimal("0.2")),
        entry_order_id=uuid4(),
        strategy_id="test_strategy",
        status=TradeStatus.CLOSED,
        opened_at=datetime(2026, 2, 15, 8, 0, 0),
        exit_price=Price(Decimal("58000")),
        exit_order_id=uuid4(),
        closed_at=datetime(2026, 2, 20, 10, 0, 0),
        fees=Decimal("22"),
    )
    portfolio._closed_trades.append(closed_trade)
    portfolio._equity_curve.append((datetime(2026, 3, 1), Decimal("100000")))
    portfolio._equity_curve.append((datetime(2026, 3, 15), Decimal("105000")))

    return portfolio


class TestTradeSerializaton:
    def test_round_trip_open_trade(self, symbol):
        trade = Trade.open(
            symbol=symbol,
            side=OrderSide.BUY,
            entry_price=Price(Decimal("65000")),
            quantity=Quantity(Decimal("0.1")),
            entry_order_id=uuid4(),
            strategy_id="rsi_v1",
            fees=Decimal("6.5"),
        )
        serialized = _serialize_trade(trade)
        restored = _deserialize_trade(serialized)

        assert restored.id == trade.id
        assert restored.symbol == trade.symbol
        assert restored.side == trade.side
        assert restored.entry_price == trade.entry_price
        assert restored.quantity == trade.quantity
        assert restored.status == TradeStatus.OPEN
        assert restored.exit_price is None

    def test_round_trip_closed_trade(self, symbol):
        trade = Trade.open(
            symbol=symbol,
            side=OrderSide.BUY,
            entry_price=Price(Decimal("65000")),
            quantity=Quantity(Decimal("0.1")),
            entry_order_id=uuid4(),
            strategy_id="rsi_v1",
        )
        trade.close(
            exit_price=Price(Decimal("68000")),
            exit_order_id=uuid4(),
            exit_fees=Decimal("6.8"),
        )
        serialized = _serialize_trade(trade)
        restored = _deserialize_trade(serialized)

        assert restored.status == TradeStatus.CLOSED
        assert restored.exit_price.value == Decimal("68000")
        assert restored.pnl == trade.pnl


class TestPositionSerialization:
    def test_round_trip(self, symbol):
        pos = Position(
            symbol=symbol,
            side=OrderSide.BUY,
            quantity=Quantity(Decimal("1.5")),
            average_entry_price=Price(Decimal("62000.12345678")),
            opened_at=datetime(2026, 3, 10, 14, 30),
            trade_ids=[uuid4(), uuid4()],
        )
        serialized = _serialize_position(pos)
        restored = _deserialize_position(serialized)

        assert restored.symbol == pos.symbol
        assert restored.side == pos.side
        assert restored.quantity.value == pos.quantity.value
        assert restored.average_entry_price.value == pos.average_entry_price.value
        assert len(restored.trade_ids) == 2


class TestSaveLoadState:
    def test_save_and_load(self, portfolio_with_position):
        session_id = "test_persistence_save_load"
        risk_engine = RiskEngine(RiskConfig())
        risk_engine._consecutive_losses = 3
        risk_engine._daily_loss = Decimal("500")
        risk_engine._trades_today = 5
        last_bar = datetime(2026, 3, 31, 8, 0, 0)

        try:
            path = save_state(session_id, portfolio_with_position, risk_engine, last_bar)
            assert path.exists()

            state = load_state(session_id)
            assert state is not None
            assert state.session_id == session_id
            assert state.cash == Decimal("70000")
            assert state.peak_equity == Decimal("105000")
            assert state.last_bar_timestamp == last_bar.isoformat()
            assert len(state.positions) == 1
            assert len(state.open_trades) == 1
            assert len(state.closed_trades) == 1
            assert len(state.equity_curve) == 2
            assert state.risk_state["consecutive_losses"] == 3
            assert state.risk_state["trades_today"] == 5
        finally:
            delete_state(session_id)

    def test_load_nonexistent_returns_none(self):
        assert load_state("nonexistent_session_xyz") is None

    def test_delete_state(self, portfolio_with_position):
        session_id = "test_persistence_delete"
        risk_engine = RiskEngine(RiskConfig())
        last_bar = datetime(2026, 3, 31)

        save_state(session_id, portfolio_with_position, risk_engine, last_bar)
        assert delete_state(session_id) is True
        assert delete_state(session_id) is False
        assert load_state(session_id) is None


class TestRebuildPortfolio:
    def test_rebuild_preserves_state(self, portfolio_with_position, symbol):
        session_id = "test_persistence_rebuild"
        risk_engine = RiskEngine(RiskConfig())
        last_bar = datetime(2026, 3, 31)

        try:
            save_state(session_id, portfolio_with_position, risk_engine, last_bar)
            state = load_state(session_id)
            rebuilt = rebuild_portfolio(state)

            assert rebuilt.state.cash == Decimal("70000")
            assert rebuilt.state.peak_equity == Decimal("105000")
            assert len(rebuilt.state.positions) == 1
            assert str(symbol) in rebuilt.state.positions
            pos = rebuilt.state.positions[str(symbol)]
            assert pos.quantity.value == Decimal("0.5")
            assert pos.average_entry_price.value == Decimal("60000")
            assert len(rebuilt.open_trades) == 1
            assert len(rebuilt.closed_trades) == 1
            assert len(rebuilt.get_equity_curve()) == 2
        finally:
            delete_state(session_id)


class TestRestoreRiskState:
    def test_restore_risk_state(self):
        risk_engine = RiskEngine(RiskConfig())
        risk_state = {
            "kill_switch_active": True,
            "kill_switch_reason": "Max drawdown",
            "consecutive_losses": 4,
            "daily_loss": "1500.50",
            "equity_at_day_start": "98000",
            "trades_today": 8,
            "last_reset_date": "2026-03-31",
            "edge_monitor": {
                "total_trades": 25,
                "ewma_win": 0.55,
                "ewma_pf": 1.3,
                "gross_win": 5000.0,
                "gross_loss": 3000.0,
            },
        }
        restore_risk_state(risk_engine, risk_state)

        assert risk_engine._kill_switch_active is True
        assert risk_engine._kill_switch_reason == "Max drawdown"
        assert risk_engine._consecutive_losses == 4
        assert risk_engine._daily_loss == Decimal("1500.50")
        assert risk_engine._trades_today == 8
        assert risk_engine._edge_monitor._total_trades == 25
        assert risk_engine._edge_monitor._ewma_win == 0.55
        assert risk_engine._edge_monitor._gross_win == 5000.0
