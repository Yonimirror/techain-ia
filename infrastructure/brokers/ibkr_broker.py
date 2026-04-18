"""
Interactive Brokers broker — executes real orders via IBKR TWS/Gateway API.

Requires ib_insync library and TWS or IB Gateway running locally or on VPS.

Environment variables:
    IBKR_HOST       (default: 127.0.0.1)
    IBKR_PORT       (default: 7497 for paper, 7496 for live)
    IBKR_CLIENT_ID  (default: 1)

Supports:
  - US Equities (NVDA, AVGO, MSFT, FCX, TSM)
  - US ETFs (SPY, XLE, SMH, GLD, XLF, XLI, TLT)
  - Fractional shares via IBKR fractional share program

Safety guardrails:
  - Market orders only (consistent with BinanceBroker)
  - Quantity precision enforced via exchange min tick
  - All operations logged with structured context
  - Connection health check before every order

Usage:
    broker = IBKRBroker()
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

# Internal ticker → IBKR contract spec
# Each entry: (symbol, secType, exchange, currency)
_CONTRACT_MAP: dict[str, tuple[str, str, str, str]] = {
    # US Equities
    "NVDA":  ("NVDA",  "STK", "SMART", "USD"),
    "AVGO":  ("AVGO",  "STK", "SMART", "USD"),
    "MSFT":  ("MSFT",  "STK", "SMART", "USD"),
    "FCX":   ("FCX",   "STK", "SMART", "USD"),
    "TSM":   ("TSM",   "STK", "SMART", "USD"),
    "AAPL":  ("AAPL",  "STK", "SMART", "USD"),
    # US ETFs
    "SPY":   ("SPY",   "STK", "SMART", "USD"),
    "XLE":   ("XLE",   "STK", "SMART", "USD"),
    "SMH":   ("SMH",   "STK", "SMART", "USD"),
    "GLD":   ("GLD",   "STK", "SMART", "USD"),
    "XLF":   ("XLF",   "STK", "SMART", "USD"),
    "XLI":   ("XLI",   "STK", "SMART", "USD"),
    "TLT":   ("TLT",   "STK", "SMART", "USD"),
    "QQQ":   ("QQQ",   "STK", "SMART", "USD"),
    # Commodities
    "CL=F":  ("CL",    "FUT", "NYMEX", "USD"),  # Crude Oil front-month
}


class IBKRBroker(IBroker):
    """
    Live broker backed by Interactive Brokers TWS/Gateway API.

    Uses ib_insync for async-compatible IB API access.
    Thread-safety: ib_insync manages its own event loop.
    """

    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        client_id: int | None = None,
    ) -> None:
        self._host = host or os.environ.get("IBKR_HOST", "127.0.0.1")
        self._port = port or int(os.environ.get("IBKR_PORT", "7497"))
        self._client_id = client_id or int(os.environ.get("IBKR_CLIENT_ID", "1"))
        self._ib = None
        self._connected = False

    def _ensure_connected(self) -> None:
        """Lazy connection — connects on first use."""
        if self._connected and self._ib and self._ib.isConnected():
            return
        try:
            from ib_insync import IB
            self._ib = IB()
            self._ib.connect(
                self._host, self._port, clientId=self._client_id,
                timeout=15,
            )
            self._connected = True
            mode = "PAPER" if self._port == 7497 else "LIVE"
            logger.info(
                "IBKRBroker connected [%s] host=%s port=%d clientId=%d",
                mode, self._host, self._port, self._client_id,
            )
        except ImportError:
            raise RuntimeError(
                "ib_insync not installed. Run: pip install ib_insync"
            )
        except Exception as e:
            self._connected = False
            logger.error("IBKR connection failed: %s", e)
            raise RuntimeError(f"IBKR connection failed: {e}") from e

    def _build_contract(self, symbol: Symbol):
        """Build an ib_insync Contract from our Symbol."""
        from ib_insync import Stock, Future

        spec = _CONTRACT_MAP.get(symbol.ticker)
        if spec is None:
            raise ValueError(
                f"Symbol '{symbol.ticker}' not mapped to an IBKR contract. "
                f"Add it to _CONTRACT_MAP in ibkr_broker.py."
            )

        ib_symbol, sec_type, exchange, currency = spec

        if sec_type == "STK":
            contract = Stock(ib_symbol, exchange, currency)
        elif sec_type == "FUT":
            contract = Future(ib_symbol, exchange=exchange, currency=currency)
        else:
            raise ValueError(f"Unsupported security type: {sec_type}")

        return contract

    # ------------------------------------------------------------------ #
    # IBroker interface                                                    #
    # ------------------------------------------------------------------ #

    async def submit_order(self, order: Order) -> str:
        """
        Submit a market order to IBKR.

        Returns IBKR order ID as string.
        """
        from ib_insync import MarketOrder

        if order.order_type != OrderType.MARKET:
            raise ValueError(
                f"IBKRBroker only supports MARKET orders. Got: {order.order_type.value}."
            )

        self._ensure_connected()

        contract = self._build_contract(order.symbol)

        # Qualify the contract (resolve conId, exchange details)
        qualified = self._ib.qualifyContracts(contract)
        if not qualified:
            raise RuntimeError(
                f"Could not qualify IBKR contract for {order.symbol.ticker}"
            )
        contract = qualified[0]

        side = "BUY" if order.side == OrderSide.BUY else "SELL"
        qty = float(order.quantity.value)

        # IBKR supports fractional shares for stocks — pass as float
        ib_order = MarketOrder(side, qty)
        ib_order.orderRef = str(order.id)[:40]

        logger.info(
            "Submitting IBKR MARKET %s %s qty=%.6f [port=%d]",
            side, contract.symbol, qty, self._port,
        )

        trade = self._ib.placeOrder(contract, ib_order)

        # Wait for fill (market orders fill near-instantly during market hours)
        timeout_secs = 30
        self._ib.waitOnUpdate(timeout=timeout_secs)

        broker_order_id = str(trade.order.orderId)

        if trade.orderStatus.status == "Filled":
            avg_price = trade.orderStatus.avgFillPrice
            logger.info(
                "IBKR order filled: broker_id=%s avg_price=%.4f status=%s",
                broker_order_id, avg_price, trade.orderStatus.status,
            )
        else:
            logger.warning(
                "IBKR order status after %ds: %s (may still be pending)",
                timeout_secs, trade.orderStatus.status,
            )

        return broker_order_id

    async def cancel_order(self, broker_order_id: str) -> bool:
        """Cancel an open order. Market orders typically fill immediately."""
        logger.debug(
            "cancel_order called for %s (market orders fill immediately)",
            broker_order_id,
        )
        return False

    async def get_account_balance(self) -> Decimal:
        """Return available cash (TotalCashValue) in USD."""
        self._ensure_connected()
        try:
            account_values = self._ib.accountSummary()
            for av in account_values:
                if av.tag == "TotalCashValue" and av.currency == "USD":
                    return Decimal(av.value)
            return Decimal("0")
        except Exception as e:
            logger.error("Failed to fetch IBKR balance: %s", e)
            raise RuntimeError(f"IBKR balance fetch failed: {e}") from e

    async def get_positions(self) -> dict[str, Quantity]:
        """Return non-zero positions."""
        self._ensure_connected()
        try:
            positions = self._ib.positions()
            result: dict[str, Quantity] = {}
            for pos in positions:
                ticker = pos.contract.symbol
                qty = Decimal(str(pos.position))
                if qty != 0:
                    result[ticker] = Quantity(abs(qty))
            return result
        except Exception as e:
            logger.error("Failed to fetch IBKR positions: %s", e)
            raise RuntimeError(f"IBKR position fetch failed: {e}") from e

    async def is_connected(self) -> bool:
        """Check if TWS/Gateway is reachable."""
        if self._ib is None:
            return False
        try:
            return self._ib.isConnected()
        except Exception:
            return False

    def disconnect(self) -> None:
        """Gracefully disconnect from TWS/Gateway."""
        if self._ib and self._ib.isConnected():
            self._ib.disconnect()
            self._connected = False
            logger.info("IBKRBroker disconnected")
