"""
Decision Engine — the orchestration hub of the trading system.

Flow:
    MarketDataEvent
        → run strategies → SignalEvents
        → risk evaluation → RiskApproved/RejectedEvent
        → build order → OrderSubmittedEvent
        → execution → OrderFilledEvent
        → portfolio update
"""
from __future__ import annotations
import logging
from decimal import Decimal

from core.domain.entities import Order, MarketData, PortfolioState
from core.domain.entities.order import OrderSide, OrderType
from core.domain.entities.signal import SignalDirection
from core.domain.value_objects import Price
from core.event_bus import (
    EventBus, MarketDataEvent, SignalEvent,
    RiskApprovedEvent, RiskRejectedEvent,
    OrderSubmittedEvent, OrderFilledEvent, SystemErrorEvent,
)
from core.interfaces.strategy_interface import IStrategy
from core.interfaces.risk_interface import IRiskEngine, RiskDecision
from core.interfaces.execution_interface import IExecutionEngine
from core.portfolio_engine import PortfolioEngine

logger = logging.getLogger(__name__)


class DecisionEngine:
    """
    Wires together all subsystems via the event bus.

    This is the ONLY component allowed to:
    - Call strategies
    - Call the risk engine
    - Call the execution engine
    - Update the portfolio engine

    No other component makes cross-system calls.
    """

    def __init__(
        self,
        event_bus: EventBus,
        strategies: list[IStrategy],
        risk_engine: IRiskEngine,
        execution_engine: IExecutionEngine,
        portfolio_engine: PortfolioEngine,
    ) -> None:
        self._bus = event_bus
        self._strategies = strategies
        self._risk = risk_engine
        self._execution = execution_engine
        self._portfolio = portfolio_engine

        # Register handlers on the bus
        from core.event_bus.events import EventType
        event_bus.subscribe(EventType.MARKET_DATA, self.on_market_data)  # type: ignore
        event_bus.subscribe(EventType.RISK_APPROVED, self.on_risk_approved)  # type: ignore
        event_bus.subscribe(EventType.ORDER_FILLED, self.on_order_filled)  # type: ignore

    async def _noop(self, _event) -> None:  # type: ignore
        pass

    async def on_market_data(self, event: MarketDataEvent) -> None:
        """
        Process new market data:
        1. Run all strategies to produce signals
        2. Aggregate signals by symbol+direction (conviction sizing)
        3. Evaluate each signal through risk engine
        4. Publish approval/rejection events

        execution_price: next bar open (set by backtester) or signal price (live).
        """
        market_data = event.market_data
        portfolio_state = self._portfolio.state
        self._pending_execution_price = event.execution_price

        # Collect all signals from all strategies
        all_signals = []
        for strategy in self._strategies:
            try:
                signals = strategy.generate_signals(market_data, portfolio_state)
                all_signals.extend(signals)
            except Exception as exc:
                logger.exception("Strategy %s raised: %s", strategy.strategy_id, exc)
                await self._bus.publish(SystemErrorEvent(
                    error=str(exc), component=strategy.strategy_id
                ))

        # Aggregate conviction: if N strategies agree on same symbol+direction,
        # boost the strongest signal's strength by a conviction factor.
        # FLAT signals always pass through individually (exits don't aggregate).
        entry_signals, exit_signals = [], []
        for sig in all_signals:
            (exit_signals if sig.is_exit else entry_signals).append(sig)

        # Group entries by (symbol, direction)
        conviction_groups: dict[tuple, list] = {}
        for sig in entry_signals:
            key = (str(sig.symbol), sig.direction.value)
            conviction_groups.setdefault(key, []).append(sig)

        # Pick the best signal per group and boost if multiple agree
        deduplicated_entries = []
        for key, group in conviction_groups.items():
            best = max(group, key=lambda s: s.strength)
            if len(group) > 1:
                # Conviction boost: up to 1.5x for 2 strategies, 2.0x for 3+
                conviction_factor = min(1.0 + 0.5 * (len(group) - 1), 2.0)
                logger.info(
                    "Conviction boost: %s %s | %d strategies agree | factor=%.1fx",
                    key[0], key[1], len(group), conviction_factor,
                )
                best = best  # we pass the factor via metadata for risk engine
                # Store conviction in metadata for position sizing
                meta = dict(best.metadata)
                meta["conviction_factor"] = conviction_factor
                meta["conviction_strategies"] = [s.strategy_id for s in group]
                # Create a new signal with updated metadata
                from core.domain.entities import Signal
                best = Signal.create(
                    strategy_id=best.strategy_id,
                    symbol=best.symbol,
                    direction=best.direction,
                    strength=min(best.strength * conviction_factor, 1.0),
                    price=best.price,
                    timeframe=best.timeframe,
                    metadata=meta,
                )
            deduplicated_entries.append(best)

        # Process all signals: exits first, then deduplicated entries
        for signal in exit_signals + deduplicated_entries:
            await self._bus.publish(SignalEvent(signal=signal))

            decision = self._risk.evaluate(signal, portfolio_state)

            if isinstance(decision, RiskDecision):
                await self._bus.publish(RiskApprovedEvent(decision=decision))
            else:
                await self._bus.publish(RiskRejectedEvent(rejection=decision))
                logger.info("Signal rejected: %s | reason=%s", signal, decision.reason)

    async def on_risk_approved(self, event: RiskApprovedEvent) -> None:
        """
        Convert approved risk decision into an order and execute it.
        """
        decision = event.decision
        signal = decision.signal

        # FLAT signal — close existing position
        if signal.direction == SignalDirection.FLAT:
            existing_pos = self._portfolio.state.get_position(signal.symbol)
            if not existing_pos:
                return
            close_side = OrderSide.SELL if existing_pos.side == OrderSide.BUY else OrderSide.BUY
            order = Order.create_market(
                symbol=signal.symbol,
                side=close_side,
                quantity=existing_pos.quantity,
                strategy_id=signal.strategy_id,
                signal_id=signal.id,
            )
            execution_price = getattr(self, "_pending_execution_price", None) or signal.price
            order.limit_price = execution_price
            await self._bus.publish(OrderSubmittedEvent(order=order))
            result = await self._execution.execute(order)
            if result.success:
                await self._bus.publish(OrderFilledEvent(order=result.order))
            else:
                logger.error("Close order failed: %s | error=%s", order.id, result.error_message)
                await self._bus.publish(SystemErrorEvent(
                    error=result.error_message or "execution_failed",
                    component="execution_engine",
                ))
            return

        # Determine order side from signal direction
        if signal.direction == SignalDirection.LONG:
            side = OrderSide.BUY
        elif signal.direction == SignalDirection.SHORT:
            side = OrderSide.SELL
        else:
            return

        order = Order.create_market(
            symbol=signal.symbol,
            side=side,
            quantity=decision.approved_quantity,
            strategy_id=signal.strategy_id,
            signal_id=signal.id,
        )

        # Execution price: next bar open (backtesting) or signal price (live)
        execution_price = getattr(self, "_pending_execution_price", None) or signal.price
        order.limit_price = execution_price

        # Carry ATR ratio from signal to order so execution engine can apply dynamic slippage
        if "atr_ratio" in signal.metadata:
            order.metadata["atr_ratio"] = signal.metadata["atr_ratio"]

        await self._bus.publish(OrderSubmittedEvent(order=order))

        result = await self._execution.execute(order)

        if result.success:
            await self._bus.publish(OrderFilledEvent(order=result.order))
        else:
            logger.error(
                "Order execution failed: %s | error=%s",
                order.id, result.error_message,
            )
            await self._bus.publish(SystemErrorEvent(
                error=result.error_message or "execution_failed",
                component="execution_engine",
            ))

    async def on_order_filled(self, event: OrderFilledEvent) -> None:
        """Update portfolio when an order is filled."""
        fees = event.order.metadata.get("fees", Decimal("0"))
        if not isinstance(fees, Decimal):
            fees = Decimal(str(fees))

        closed_before = len(self._portfolio.closed_trades)
        self._portfolio.process_fill(event.order, fees)
        closed_after = len(self._portfolio.closed_trades)

        # If a trade just closed, report its P&L to the risk engine for circuit breakers
        if closed_after > closed_before and hasattr(self._risk, "record_trade_result"):
            last_trade = self._portfolio.closed_trades[-1]
            self._risk.record_trade_result(last_trade.pnl)

        logger.info(
            "Portfolio updated after fill: equity~=%s",
            self._portfolio.state.total_equity(),
        )
