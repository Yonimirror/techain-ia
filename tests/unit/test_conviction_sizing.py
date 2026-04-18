"""Tests for conviction sizing in decision engine (Feature 2)."""
import pytest
from decimal import Decimal
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

from core.domain.entities import Signal, PortfolioState, MarketData
from core.domain.entities.signal import SignalDirection
from core.domain.value_objects import Symbol, Price, Timeframe, Quantity
from core.interfaces.risk_interface import RiskDecision, RiskRejection, RejectionReason
from core.event_bus import EventBus, MarketDataEvent, SignalEvent
from core.decision_engine import DecisionEngine


def _signal(
    strategy_id: str,
    symbol: str = "BTC",
    direction: SignalDirection = SignalDirection.LONG,
    strength: float = 0.6,
) -> Signal:
    return Signal.create(
        strategy_id=strategy_id,
        symbol=Symbol.of(symbol, "BINANCE"),
        direction=direction,
        strength=strength,
        price=Price.of(50000.0),
        timeframe=Timeframe.D1,
    )


def _make_engine(strategies, risk_returns=None):
    """Build DecisionEngine with mock subsystems."""
    bus = EventBus()
    risk = MagicMock()
    if risk_returns:
        risk.evaluate.side_effect = risk_returns
    else:
        risk.evaluate.return_value = RiskDecision(
            signal=_signal("x"),
            approved_quantity=Quantity(Decimal("1")),
            risk_score=0.3,
        )
    execution = AsyncMock()
    execution.execute.return_value = MagicMock(success=False, error_message="test")
    portfolio = MagicMock()
    portfolio.state = PortfolioState(
        cash=Decimal("100000"),
        initial_capital=Decimal("100000"),
    )
    engine = DecisionEngine(bus, strategies, risk, execution, portfolio)
    return engine, bus, risk


class TestConvictionAggregation:
    @pytest.mark.asyncio
    async def test_single_strategy_no_boost(self):
        """One strategy signal passes through without conviction boost."""
        sig = _signal("strat_a", strength=0.6)
        strat = MagicMock()
        strat.strategy_id = "strat_a"
        strat.generate_signals.return_value = [sig]

        engine, bus, risk = _make_engine([strat])

        md = MagicMock(spec=MarketData)
        event = MarketDataEvent(market_data=md, execution_price=Price.of(50000))
        await engine.on_market_data(event)

        # Risk should be called once with the original signal
        assert risk.evaluate.call_count == 1
        evaluated_signal = risk.evaluate.call_args[0][0]
        assert evaluated_signal.strength == pytest.approx(0.6, abs=0.01)

    @pytest.mark.asyncio
    async def test_two_strategies_boost_15x(self):
        """Two strategies agreeing → 1.5x conviction boost."""
        strat_a = MagicMock()
        strat_a.strategy_id = "strat_a"
        strat_a.generate_signals.return_value = [
            _signal("strat_a", strength=0.6),
        ]
        strat_b = MagicMock()
        strat_b.strategy_id = "strat_b"
        strat_b.generate_signals.return_value = [
            _signal("strat_b", strength=0.5),
        ]

        engine, bus, risk = _make_engine([strat_a, strat_b])

        md = MagicMock(spec=MarketData)
        event = MarketDataEvent(market_data=md, execution_price=Price.of(50000))
        await engine.on_market_data(event)

        # Only one call to risk (deduplicated)
        assert risk.evaluate.call_count == 1
        evaluated_signal = risk.evaluate.call_args[0][0]
        # Best strength (0.6) * 1.5 = 0.9
        assert evaluated_signal.strength == pytest.approx(0.9, abs=0.01)
        assert "conviction_factor" in evaluated_signal.metadata
        assert evaluated_signal.metadata["conviction_factor"] == pytest.approx(1.5)

    @pytest.mark.asyncio
    async def test_three_strategies_boost_capped_at_2x(self):
        """Three strategies agreeing → 2.0x boost (capped)."""
        strategies = []
        for i in range(3):
            s = MagicMock()
            s.strategy_id = f"strat_{i}"
            s.generate_signals.return_value = [
                _signal(f"strat_{i}", strength=0.5),
            ]
            strategies.append(s)

        engine, bus, risk = _make_engine(strategies)

        md = MagicMock(spec=MarketData)
        event = MarketDataEvent(market_data=md, execution_price=Price.of(50000))
        await engine.on_market_data(event)

        assert risk.evaluate.call_count == 1
        evaluated_signal = risk.evaluate.call_args[0][0]
        # 0.5 * 2.0 = 1.0 (also capped at 1.0)
        assert evaluated_signal.strength == pytest.approx(1.0, abs=0.01)
        assert evaluated_signal.metadata["conviction_factor"] == pytest.approx(2.0)

    @pytest.mark.asyncio
    async def test_different_symbols_not_aggregated(self):
        """Signals for different symbols are NOT aggregated."""
        strat_a = MagicMock()
        strat_a.strategy_id = "strat_a"
        strat_a.generate_signals.return_value = [
            _signal("strat_a", symbol="BTC", strength=0.6),
        ]
        strat_b = MagicMock()
        strat_b.strategy_id = "strat_b"
        strat_b.generate_signals.return_value = [
            _signal("strat_b", symbol="ETH", strength=0.5),
        ]

        engine, bus, risk = _make_engine([strat_a, strat_b])

        md = MagicMock(spec=MarketData)
        event = MarketDataEvent(market_data=md, execution_price=Price.of(50000))
        await engine.on_market_data(event)

        # Two separate signals → two risk evaluations
        assert risk.evaluate.call_count == 2

    @pytest.mark.asyncio
    async def test_different_directions_not_aggregated(self):
        """LONG and SHORT signals for same symbol are NOT aggregated."""
        strat_a = MagicMock()
        strat_a.strategy_id = "strat_a"
        strat_a.generate_signals.return_value = [
            _signal("strat_a", direction=SignalDirection.LONG),
        ]
        strat_b = MagicMock()
        strat_b.strategy_id = "strat_b"
        strat_b.generate_signals.return_value = [
            _signal("strat_b", direction=SignalDirection.SHORT),
        ]

        engine, bus, risk = _make_engine([strat_a, strat_b])

        md = MagicMock(spec=MarketData)
        event = MarketDataEvent(market_data=md, execution_price=Price.of(50000))
        await engine.on_market_data(event)

        assert risk.evaluate.call_count == 2

    @pytest.mark.asyncio
    async def test_exit_signals_not_aggregated(self):
        """FLAT (exit) signals pass through individually, never aggregated."""
        strat_a = MagicMock()
        strat_a.strategy_id = "strat_a"
        strat_a.generate_signals.return_value = [
            _signal("strat_a", direction=SignalDirection.FLAT),
        ]
        strat_b = MagicMock()
        strat_b.strategy_id = "strat_b"
        strat_b.generate_signals.return_value = [
            _signal("strat_b", direction=SignalDirection.FLAT),
        ]

        engine, bus, risk = _make_engine([strat_a, strat_b])

        md = MagicMock(spec=MarketData)
        event = MarketDataEvent(market_data=md, execution_price=Price.of(50000))
        await engine.on_market_data(event)

        # Both exit signals processed individually
        assert risk.evaluate.call_count == 2

    @pytest.mark.asyncio
    async def test_exits_processed_before_entries(self):
        """Exit signals are processed before entry signals."""
        call_order = []

        strat = MagicMock()
        strat.strategy_id = "strat_a"
        exit_sig = _signal("strat_a", direction=SignalDirection.FLAT)
        entry_sig = _signal("strat_a", symbol="ETH", direction=SignalDirection.LONG)
        strat.generate_signals.return_value = [entry_sig, exit_sig]

        engine, bus, risk = _make_engine([strat])

        original_evaluate = risk.evaluate

        def track_evaluate(signal, portfolio):
            call_order.append(signal.direction)
            return RiskDecision(
                signal=signal,
                approved_quantity=Quantity(Decimal("1")),
                risk_score=0.3,
            )

        risk.evaluate.side_effect = track_evaluate

        md = MagicMock(spec=MarketData)
        event = MarketDataEvent(market_data=md, execution_price=Price.of(50000))
        await engine.on_market_data(event)

        assert call_order[0] == SignalDirection.FLAT
        assert call_order[1] == SignalDirection.LONG

    @pytest.mark.asyncio
    async def test_strength_capped_at_1(self):
        """Boosted strength should never exceed 1.0."""
        strategies = []
        for i in range(3):
            s = MagicMock()
            s.strategy_id = f"strat_{i}"
            s.generate_signals.return_value = [
                _signal(f"strat_{i}", strength=0.9),
            ]
            strategies.append(s)

        engine, bus, risk = _make_engine(strategies)

        md = MagicMock(spec=MarketData)
        event = MarketDataEvent(market_data=md, execution_price=Price.of(50000))
        await engine.on_market_data(event)

        evaluated_signal = risk.evaluate.call_args[0][0]
        # 0.9 * 2.0 = 1.8 → capped at 1.0
        assert evaluated_signal.strength <= 1.0
