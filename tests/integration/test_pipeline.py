"""
Integration test: full pipeline signal → risk → execution → portfolio.

Uses PaperBroker and in-memory EventBus.
No real broker connections.
"""
import pytest
import asyncio
from decimal import Decimal
from datetime import datetime

from core.domain.entities import MarketData, OHLCV
from core.domain.value_objects import Symbol, Price, Quantity, Timeframe
from core.event_bus import EventBus, MarketDataEvent
from core.strategies import EMACrossoverStrategy
from core.risk_engine import RiskEngine, RiskConfig
from core.execution_engine import ExecutionEngine, PaperBroker
from core.portfolio_engine import PortfolioEngine
from core.decision_engine import DecisionEngine


def make_trending_market_data(n_bars: int = 50, start_price: float = 100.0) -> MarketData:
    """Create uptrending market data to trigger EMA crossover signals."""
    symbol = Symbol.of("TEST", "TEST")
    bars = []
    price = start_price
    ts = datetime(2024, 1, 1)

    from datetime import timedelta
    for i in range(n_bars):
        open_p = price
        close_p = price * (1.015 if i < n_bars // 2 else 1.005)  # fast rise then slow
        high_p = close_p * 1.002
        low_p = open_p * 0.998
        bars.append(OHLCV(
            timestamp=ts + timedelta(days=i),
            open=Price.of(open_p),
            high=Price.of(high_p),
            low=Price.of(low_p),
            close=Price.of(close_p),
            volume=Quantity.of(10000),
        ))
        price = close_p

    return MarketData(symbol=symbol, timeframe=Timeframe.D1, bars=bars)


@pytest.mark.asyncio
async def test_full_pipeline_runs_without_error():
    """Full pipeline completes without raising exceptions."""
    initial_capital = Decimal("100000")
    broker = PaperBroker(initial_cash=initial_capital)
    portfolio = PortfolioEngine(initial_capital)
    execution = ExecutionEngine(broker)
    risk = RiskEngine(RiskConfig(max_trades_per_day=50))
    strategies = [EMACrossoverStrategy()]

    bus = EventBus()
    engine = DecisionEngine(
        event_bus=bus,
        strategies=strategies,
        risk_engine=risk,
        execution_engine=execution,
        portfolio_engine=portfolio,
    )

    market_data = make_trending_market_data(60)

    for i in range(len(market_data.bars)):
        slice_data = MarketData(
            symbol=market_data.symbol,
            timeframe=market_data.timeframe,
            bars=market_data.bars[:i + 1],
        )
        await bus.publish(MarketDataEvent(market_data=slice_data))

    # Portfolio state should be valid
    state = portfolio.state
    assert state.cash >= Decimal("0")
    equity = state.total_equity()
    assert equity > Decimal("0")


@pytest.mark.asyncio
async def test_risk_engine_limits_respected():
    """Risk engine prevents exceeding exposure limits."""
    initial_capital = Decimal("10000")
    broker = PaperBroker(initial_cash=initial_capital)
    portfolio = PortfolioEngine(initial_capital)
    execution = ExecutionEngine(broker)
    risk = RiskEngine(RiskConfig(
        max_total_exposure_pct=50.0,
        max_trades_per_day=5,
    ))
    strategies = [EMACrossoverStrategy()]

    bus = EventBus()
    engine = DecisionEngine(
        event_bus=bus,
        strategies=strategies,
        risk_engine=risk,
        execution_engine=execution,
        portfolio_engine=portfolio,
    )

    market_data = make_trending_market_data(60)

    for i in range(len(market_data.bars)):
        slice_data = MarketData(
            symbol=market_data.symbol,
            timeframe=market_data.timeframe,
            bars=market_data.bars[:i + 1],
        )
        await bus.publish(MarketDataEvent(market_data=slice_data))

    # Exposure should never exceed configured limit
    state = portfolio.state
    invested = sum(p.notional_value for p in state.positions.values())
    equity = state.total_equity()
    if equity > 0:
        exposure_pct = float(invested / equity * 100)
        assert exposure_pct <= 55.0  # some tolerance for market moves


@pytest.mark.asyncio
async def test_event_bus_stats():
    """Event bus tracks published events correctly."""
    bus = EventBus()
    from core.event_bus.events import EventType

    count = 0

    async def counter(event):
        nonlocal count
        count += 1

    bus.subscribe(EventType.MARKET_DATA, counter)

    market_data = make_trending_market_data(5)
    for bar in market_data.bars:
        slice_data = MarketData(
            symbol=market_data.symbol,
            timeframe=market_data.timeframe,
            bars=[bar],
        )
        await bus.publish(MarketDataEvent(market_data=slice_data))

    assert bus.stats["published"].get(EventType.MARKET_DATA, 0) == 5
    assert count == 5
