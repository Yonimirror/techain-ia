"""
Paper trading broker — simulates fills with slippage and fees.
Used for backtesting and paper trading modes.
"""
from __future__ import annotations
import logging
from decimal import Decimal

from core.domain.entities import Order
from core.domain.entities.order import OrderStatus
from core.domain.value_objects import Price, Quantity
from core.interfaces.broker_interface import IBroker

logger = logging.getLogger(__name__)


class PaperBroker(IBroker):
    """
    Simulated broker for paper trading and backtesting.

    Slippage model: market orders filled at close ± slippage_bps.
    Fee model: flat fee_bps on notional value.
    """

    def __init__(
        self,
        initial_cash: Decimal = Decimal("100000"),
        slippage_bps: float = 5.0,    # 5 basis points slippage on market orders
        fee_bps: float = 10.0,         # 10 bps commission
    ) -> None:
        self._cash = initial_cash
        self._slippage_bps = Decimal(str(slippage_bps)) / Decimal("10000")
        self._fee_bps = Decimal(str(fee_bps)) / Decimal("10000")
        self._positions: dict[str, Quantity] = {}
        self._order_counter: int = 0

    async def submit_order(self, order: Order) -> str:
        self._order_counter += 1
        broker_id = f"PAPER-{self._order_counter:06d}"
        logger.debug("Paper broker received order %s → %s", order.id, broker_id)
        return broker_id

    async def cancel_order(self, broker_order_id: str) -> bool:
        logger.debug("Paper broker cancelled %s", broker_order_id)
        return True

    async def get_account_balance(self) -> Decimal:
        return self._cash

    async def get_positions(self) -> dict[str, Quantity]:
        return dict(self._positions)

    async def is_connected(self) -> bool:
        return True

    def simulate_fill(
        self,
        order: Order,
        market_price: Price,
        atr_ratio: float = 1.0,
    ) -> tuple[Price, Decimal]:
        """
        Simulate fill price and fees for a market/limit order.

        atr_ratio: current ATR / rolling mean ATR.
            > 1.0 → high volatility → slippage scales up (capped at 3x)
            < 1.0 → low volatility  → slippage scales down (floor at 0.25x)

        Returns (fill_price, fees).
        """
        from core.domain.entities.order import OrderSide, OrderType

        fill_price = market_price

        if order.order_type == OrderType.MARKET:
            vol_factor = Decimal(str(min(max(atr_ratio, 0.25), 3.0)))
            slippage = market_price.value * self._slippage_bps * vol_factor
            if order.side == OrderSide.BUY:
                fill_price = Price(market_price.value + slippage)
            else:
                fill_price = Price(market_price.value - slippage)
        elif order.order_type == OrderType.LIMIT and order.limit_price is not None:
            fill_price = order.limit_price

        notional = fill_price.value * order.quantity.value
        fees = notional * self._fee_bps

        return fill_price, fees
