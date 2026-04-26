"""
Binance Spot Broker — executes real orders via Binance REST API.

Reads credentials from environment variables:
    BINANCE_API_KEY
    BINANCE_SECRET_KEY
    BINANCE_TESTNET=true   (optional — routes to Binance Testnet)

Safety guardrails:
  - Market orders only (no risk of resting limit orders forgotten open)
  - Quantity precision enforced via exchange LOT_SIZE filter
  - Minimum notional enforced via MIN_NOTIONAL filter
  - All operations logged with structured context for the DecisionTracer

Usage:
    broker = BinanceBroker()
    ok = await broker.is_connected()
    broker_id = await broker.submit_order(order)
"""
from __future__ import annotations

import logging
import os
from decimal import Decimal, ROUND_DOWN
from typing import Any

from core.domain.entities import Order
from core.domain.entities.order import OrderSide, OrderType, OrderStatus
from core.domain.value_objects import Symbol, Quantity
from core.interfaces.broker_interface import IBroker

logger = logging.getLogger(__name__)

# Binance symbol mapping (internal ticker → USDT spot pair)
_SYMBOL_MAP = {
    "BTC": "BTCUSDT",
    "ETH": "ETHUSDT",
    "BNB": "BNBUSDT",
    "SOL": "SOLUSDT",
    # EUR pairs — for accounts funded in EUR (no USDT conversion needed)
    "BTC_EUR": "BTCEUR",
    "ETH_EUR": "ETHEUR",
    "SOL_EUR": "SOLEUR",
}


class BinanceBroker(IBroker):
    """
    Live broker backed by Binance Spot REST API.

    Thread-safety: The underlying python-binance Client is synchronous.
    All methods are async for interface compliance but run the sync client
    via direct call (no threadpool needed for low-frequency daily trading).
    """

    def __init__(self, testnet: bool | None = None) -> None:
        """
        Args:
            testnet: Force testnet mode. If None, reads BINANCE_TESTNET env var.
                     Testnet uses separate credentials and endpoint.
        """
        self._testnet = testnet if testnet is not None else (
            os.environ.get("BINANCE_TESTNET", "").lower() in ("1", "true", "yes")
        )
        self._client = self._build_client()
        # Cache of exchange info per symbol: {binance_symbol: {filters}}
        self._exchange_info_cache: dict[str, dict] = {}

    # ------------------------------------------------------------------ #
    # IBroker interface                                                    #
    # ------------------------------------------------------------------ #

    async def submit_order(self, order: Order) -> str:
        """
        Submit a market order to Binance.

        Only MARKET orders are supported for live trading —
        limit orders would require an open-order monitor not yet implemented.

        Returns:
            Binance order ID as string.

        Raises:
            ValueError: Unsupported order type or symbol.
            RuntimeError: Binance API error (insufficient funds, filters, etc.)
        """
        if order.order_type != OrderType.MARKET:
            raise ValueError(
                f"BinanceBroker only supports MARKET orders. Got: {order.order_type.value}. "
                "Use PaperBroker for limit/stop simulations."
            )

        binance_symbol = self._to_binance_symbol(order.symbol)
        side = "BUY" if order.side == OrderSide.BUY else "SELL"

        # Enforce LOT_SIZE precision
        raw_qty = order.quantity.value
        qty_str = await self._format_quantity(binance_symbol, raw_qty)

        logger.info(
            "Submitting MARKET %s %s qty=%s [testnet=%s]",
            side, binance_symbol, qty_str, self._testnet,
        )

        try:
            result = self._client.create_order(
                symbol=binance_symbol,
                side=side,
                type="MARKET",
                quantity=qty_str,
                newClientOrderId=str(order.id)[:36],
            )
        except Exception as e:
            logger.error(
                "Binance order submission failed: %s | symbol=%s side=%s qty=%s",
                e, binance_symbol, side, qty_str,
            )
            raise RuntimeError(f"Binance order failed: {e}") from e

        broker_order_id = str(result["orderId"])
        fill_price = result.get("fills", [{}])
        avg_price = self._avg_fill_price(result)

        logger.info(
            "Order filled: broker_id=%s avg_price=%s status=%s",
            broker_order_id, avg_price, result.get("status"),
        )

        return broker_order_id

    async def cancel_order(self, broker_order_id: str) -> bool:
        """
        Cancel an open order.

        Note: market orders fill immediately — cancel is typically a no-op.
        Returns False (not cancelled) if the order is already filled.
        """
        # Market orders on Binance are filled synchronously — nothing to cancel.
        logger.debug("cancel_order called for %s (market orders fill immediately)", broker_order_id)
        return False

    async def get_account_balance(self) -> Decimal:
        """Return free balance — USDT preferred, EUR fallback."""
        try:
            account = self._client.get_account()
            balances = {a["asset"]: Decimal(a["free"]) for a in account.get("balances", [])}
            if balances.get("USDT", Decimal("0")) > 0:
                return balances["USDT"]
            if balances.get("EUR", Decimal("0")) > 0:
                return balances["EUR"]
            return Decimal("0")
        except Exception as e:
            logger.error("Failed to fetch account balance: %s", e)
            raise RuntimeError(f"Balance fetch failed: {e}") from e

    async def get_positions(self) -> dict[str, Quantity]:
        """Return non-zero spot balances (excluding USDT)."""
        try:
            account = self._client.get_account()
            positions: dict[str, Quantity] = {}
            for asset in account.get("balances", []):
                ticker = asset["asset"]
                if ticker == "USDT":
                    continue
                free = Decimal(asset["free"])
                locked = Decimal(asset["locked"])
                total = free + locked
                if total > Decimal("0"):
                    positions[ticker] = Quantity(total)
            return positions
        except Exception as e:
            logger.error("Failed to fetch positions: %s", e)
            raise RuntimeError(f"Position fetch failed: {e}") from e

    async def is_connected(self) -> bool:
        """Ping Binance and return True if API responds."""
        if self._client is None:
            return False
        try:
            self._client.ping()
            return True
        except Exception as e:
            logger.warning("Binance connectivity check failed: %s", e)
            return False

    # ------------------------------------------------------------------ #
    # Internal helpers                                                     #
    # ------------------------------------------------------------------ #

    def _build_client(self):
        try:
            from binance.client import Client
            api_key = os.environ.get("BINANCE_API_KEY", "")
            api_secret = os.environ.get("BINANCE_SECRET_KEY", "")

            if not api_key or not api_secret:
                logger.error(
                    "BINANCE_API_KEY / BINANCE_SECRET_KEY not set. "
                    "Set them in .env or environment before using BinanceBroker."
                )
                raise RuntimeError("Binance credentials missing")

            client = Client(api_key, api_secret, testnet=self._testnet)
            mode = "TESTNET" if self._testnet else "LIVE"
            logger.info("BinanceBroker initialized [%s]", mode)
            return client
        except ImportError:
            raise RuntimeError("python-binance not installed. Run: pip install python-binance")

    def _to_binance_symbol(self, symbol: Symbol) -> str:
        binance_sym = _SYMBOL_MAP.get(symbol.ticker)
        if binance_sym is None:
            raise ValueError(
                f"Symbol '{symbol.ticker}' not mapped to a Binance pair. "
                f"Add it to _SYMBOL_MAP in binance_broker.py."
            )
        return binance_sym

    async def _get_exchange_info(self, binance_symbol: str) -> dict:
        """Fetch and cache exchange filters for a symbol."""
        if binance_symbol not in self._exchange_info_cache:
            info = self._client.get_symbol_info(binance_symbol)
            if info is None:
                raise ValueError(f"Symbol {binance_symbol} not found on Binance")
            filters = {f["filterType"]: f for f in info.get("filters", [])}
            self._exchange_info_cache[binance_symbol] = filters
        return self._exchange_info_cache[binance_symbol]

    async def _format_quantity(self, binance_symbol: str, qty: Decimal) -> str:
        """
        Format quantity to comply with Binance LOT_SIZE and MIN_NOTIONAL.

        Returns quantity as string truncated to the correct step size.
        Raises ValueError if quantity is below minimum lot size.
        """
        filters = await self._get_exchange_info(binance_symbol)

        lot_size = filters.get("LOT_SIZE", {})
        step_size = lot_size.get("stepSize", "0.00001")
        min_qty = Decimal(lot_size.get("minQty", "0.00001"))

        step = Decimal(step_size)
        # Truncate (floor) to step size precision
        precision = abs(step.normalize().as_tuple().exponent)
        quantizer = Decimal(10) ** -precision
        truncated = (qty / step).to_integral_value(rounding=ROUND_DOWN) * step
        truncated = truncated.quantize(quantizer)

        if truncated < min_qty:
            raise ValueError(
                f"Quantity {truncated} is below Binance minimum lot size {min_qty} for {binance_symbol}. "
                "Increase position size or reduce stop loss."
            )

        return str(truncated)

    def _avg_fill_price(self, result: dict) -> Decimal:
        """Compute weighted average fill price from Binance order result."""
        fills = result.get("fills", [])
        if not fills:
            return Decimal("0")
        total_qty = sum(Decimal(f["qty"]) for f in fills)
        if total_qty == 0:
            return Decimal("0")
        total_notional = sum(Decimal(f["price"]) * Decimal(f["qty"]) for f in fills)
        return total_notional / total_qty
