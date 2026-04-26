from __future__ import annotations
import logging
from decimal import Decimal

from core.domain.entities import Order
from core.domain.entities.order import OrderStatus, OrderSide
from core.domain.value_objects import Price, Quantity
from core.interfaces.execution_interface import IExecutionEngine, OrderResult
from core.interfaces.broker_interface import IBroker
from core.execution_engine.paper_broker import PaperBroker

logger = logging.getLogger(__name__)


async def _notify_trade(order: Order, filled_price: Price | None = None) -> None:
    """Fire-and-forget Telegram alert for live fills."""
    try:
        from apps.telegram_bot.bot import TelegramNotifier
        notifier = TelegramNotifier()
        if not notifier.enabled:
            return
        side = "COMPRA" if order.side == OrderSide.BUY else "VENTA"
        price_str = str(filled_price.value) if filled_price else "market"
        pnl = order.metadata.get("pnl_pct", "")
        pnl_str = f"{pnl:+.2f}%" if pnl else ""
        await notifier.send_trade_alert(
            action="ORDEN EJECUTADA",
            symbol=order.symbol.ticker,
            side=side,
            qty=str(order.quantity.value),
            price=price_str,
            pnl=pnl_str,
        )
    except Exception as exc:
        logger.debug("Telegram trade alert failed: %s", exc)


class ExecutionEngine(IExecutionEngine):
    """
    Execution engine: bridges the decision layer to the broker.

    Responsibilities:
    - Submit orders to broker
    - Handle fill confirmation
    - Update order state
    - Compute fees

    In paper trading mode: fills are simulated immediately.
    In live mode: connects to real broker via IBroker implementation.
    """

    def __init__(self, broker: IBroker) -> None:
        self._broker = broker

    async def execute(self, order: Order) -> OrderResult:
        logger.info("Executing order: %s", order)

        try:
            broker_id = await self._broker.submit_order(order)
            order.broker_order_id = broker_id
            order.status = OrderStatus.SUBMITTED
        except Exception as exc:
            logger.error("Order submission failed: %s | error=%s", order, exc)
            order.reject(str(exc))
            return OrderResult(order=order, success=False, error_message=str(exc))

        # Paper broker: simulate immediate fill
        if isinstance(self._broker, PaperBroker):
            return await self._simulate_paper_fill(order)

        # Live fill — notify via Telegram
        await _notify_trade(order)
        return OrderResult(order=order, success=True, broker_order_id=broker_id)

    async def _simulate_paper_fill(self, order: Order) -> OrderResult:
        """Immediate fill simulation for paper broker."""
        assert isinstance(self._broker, PaperBroker)

        # Use limit or a neutral price fallback for simulation
        reference_price = order.limit_price or Price.of("0")
        if reference_price.value == Decimal("0"):
            logger.warning("No reference price for paper fill of %s", order.id)
            order.reject("No reference price for paper fill")
            return OrderResult(order=order, success=False, error_message="No reference price")

        atr_ratio = float(order.metadata.get("atr_ratio", 1.0))
        fill_price, fees = self._broker.simulate_fill(order, reference_price, atr_ratio=atr_ratio)
        order.fill(fill_price, order.quantity)
        order.metadata["fees"] = fees  # pass fees to portfolio engine

        logger.info(
            "Paper fill: %s | price=%s | fees=%s",
            order.id, fill_price, fees,
        )

        return OrderResult(
            order=order,
            success=True,
            filled_price=fill_price,
            filled_quantity=order.quantity,
            broker_order_id=order.broker_order_id,
            fees=fees,
        )

    async def cancel(self, order: Order) -> bool:
        if not order.is_active:
            return False
        success = await self._broker.cancel_order(order.broker_order_id or "")
        if success:
            order.status = OrderStatus.CANCELLED
        return success

    async def get_order_status(self, order: Order) -> Order:
        return order
