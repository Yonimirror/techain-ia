from __future__ import annotations
import logging
from datetime import datetime, timezone
from decimal import Decimal

from core.domain.entities import Order, Trade, Position, PortfolioState
from core.domain.entities.order import OrderStatus, OrderSide
from core.domain.entities.trade import TradeStatus
from core.domain.value_objects import Price

logger = logging.getLogger(__name__)


class PortfolioEngine:
    """
    Tracks all positions, trades, and capital.

    Responsibilities:
    - Process filled orders → open/close positions
    - Calculate realized PnL
    - Maintain portfolio state
    - Provide equity snapshots
    """

    def __init__(self, initial_capital: Decimal) -> None:
        self._state = PortfolioState(
            cash=initial_capital,
            initial_capital=initial_capital,
        )
        self._open_trades: dict[str, Trade] = {}  # symbol_str -> Trade
        self._closed_trades: list[Trade] = []
        self._equity_curve: list[tuple[datetime, Decimal]] = []

    @property
    def state(self) -> PortfolioState:
        return self._state

    @property
    def closed_trades(self) -> list[Trade]:
        return list(self._closed_trades)

    @property
    def open_trades(self) -> dict[str, Trade]:
        return dict(self._open_trades)

    def process_fill(self, order: Order, fees: Decimal = Decimal("0")) -> None:
        """Update portfolio state when an order is filled."""
        if order.status != OrderStatus.FILLED:
            return
        if order.filled_price is None or order.filled_quantity is None:
            logger.error("Filled order %s missing price/quantity", order.id)
            return

        symbol_str = str(order.symbol)
        fill_price = order.filled_price
        fill_qty = order.filled_quantity
        notional = fill_price.value * fill_qty.value

        if order.side == OrderSide.BUY:
            self._handle_buy(order, symbol_str, fill_price, fill_qty, notional, fees)
        else:
            self._handle_sell(order, symbol_str, fill_price, fill_qty, notional, fees)

        self._state.update_timestamp()
        self._record_equity()
        logger.info("Portfolio updated: %s", self._state)

    def _handle_buy(
        self,
        order: Order,
        symbol_str: str,
        fill_price: Price,
        fill_qty,
        notional: Decimal,
        fees: Decimal,
    ) -> None:
        existing = self._state.get_position(order.symbol)

        if existing and existing.side == OrderSide.SELL:
            # Closing a SHORT position — return margin + PnL
            pnl = (existing.average_entry_price.value - fill_price.value) * fill_qty.value
            # Return the original margin (notional at entry) + PnL - fees
            entry_notional = existing.average_entry_price.value * fill_qty.value
            self._state.cash += entry_notional + pnl - fees

            existing.reduce_quantity(fill_qty)
            if existing.is_flat:
                self._state.set_position(existing)  # removes if flat

            # Close trade record
            open_trade = self._open_trades.pop(symbol_str, None)
            if open_trade:
                open_trade.close(
                    exit_price=fill_price,
                    exit_order_id=order.id,
                    closed_at=order.filled_at,
                    exit_fees=fees,
                )
                self._closed_trades.append(open_trade)
                logger.info("SHORT trade closed: %s | PnL=%s", open_trade, open_trade.pnl)
        else:
            # Opening a LONG position
            cost = notional + fees
            if cost > self._state.cash:
                logger.error(
                    "Insufficient cash for buy order %s (cost=%s, cash=%s) — order rejected",
                    order.id, cost, self._state.cash,
                )
                return

            self._state.cash -= cost

            if existing:
                existing.add_quantity(fill_qty, fill_price)
            else:
                pos = Position(
                    symbol=order.symbol,
                    side=OrderSide.BUY,
                    quantity=fill_qty,
                    average_entry_price=fill_price,
                    opened_at=order.filled_at or datetime.now(timezone.utc),
                    trade_ids=[order.id],
                )
                self._state.set_position(pos)

            # Open trade record
            trade = Trade.open(
                symbol=order.symbol,
                side=OrderSide.BUY,
                entry_price=fill_price,
                quantity=fill_qty,
                entry_order_id=order.id,
                strategy_id=order.strategy_id or "unknown",
                opened_at=order.filled_at,
                fees=fees,
            )
            self._open_trades[symbol_str] = trade

    def _handle_sell(
        self,
        order: Order,
        symbol_str: str,
        fill_price: Price,
        fill_qty,
        notional: Decimal,
        fees: Decimal,
    ) -> None:
        existing_pos = self._state.get_position(order.symbol)

        if existing_pos and existing_pos.side == OrderSide.BUY:
            # Closing a LONG position
            proceeds = notional - fees
            self._state.cash += proceeds

            existing_pos.reduce_quantity(fill_qty)
            if existing_pos.is_flat:
                self._state.set_position(existing_pos)  # removes if flat

            # Close trade record
            open_trade = self._open_trades.pop(symbol_str, None)
            if open_trade:
                open_trade.close(
                    exit_price=fill_price,
                    exit_order_id=order.id,
                    closed_at=order.filled_at,
                    exit_fees=fees,
                )
                self._closed_trades.append(open_trade)
                logger.info("LONG trade closed: %s | PnL=%s", open_trade, open_trade.pnl)
        else:
            # Opening a SHORT position — reserve margin
            margin = notional + fees
            if margin > self._state.cash:
                logger.error(
                    "Insufficient cash for short order %s (margin=%s, cash=%s) — order rejected",
                    order.id, margin, self._state.cash,
                )
                return

            self._state.cash -= margin

            if existing_pos:
                existing_pos.add_quantity(fill_qty, fill_price)
            else:
                pos = Position(
                    symbol=order.symbol,
                    side=OrderSide.SELL,
                    quantity=fill_qty,
                    average_entry_price=fill_price,
                    opened_at=order.filled_at or datetime.now(timezone.utc),
                    trade_ids=[order.id],
                )
                self._state.set_position(pos)

            # Open trade record
            trade = Trade.open(
                symbol=order.symbol,
                side=OrderSide.SELL,
                entry_price=fill_price,
                quantity=fill_qty,
                entry_order_id=order.id,
                strategy_id=order.strategy_id or "unknown",
                opened_at=order.filled_at,
                fees=fees,
            )
            self._open_trades[symbol_str] = trade

    def _record_equity(self) -> None:
        equity = self._state.total_equity()
        self._equity_curve.append((datetime.now(timezone.utc), equity))

    def get_equity_curve(self) -> list[tuple[datetime, Decimal]]:
        return list(self._equity_curve)

    def summary(self) -> dict:
        closed = self._closed_trades
        if not closed:
            return {"trades": 0}
        pnls = [float(t.pnl) for t in closed]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        return {
            "trades": len(closed),
            "win_rate": len(wins) / len(closed) * 100,
            "total_pnl": sum(pnls),
            "avg_win": sum(wins) / len(wins) if wins else 0,
            "avg_loss": sum(losses) / len(losses) if losses else 0,
            "profit_factor": abs(sum(wins) / sum(losses)) if losses and sum(losses) != 0 else float("inf"),
        }
