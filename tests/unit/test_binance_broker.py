"""
Unit tests for BinanceBroker.

All tests mock the python-binance Client — no real API calls.
"""
from __future__ import annotations

import os
import pytest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from core.domain.entities import Order
from core.domain.entities.order import OrderSide, OrderType, OrderStatus
from core.domain.value_objects import Symbol, Quantity, Price


# ------------------------------------------------------------------ #
# Helpers                                                              #
# ------------------------------------------------------------------ #

def _make_order(side: OrderSide = OrderSide.BUY, qty: float = 0.5) -> Order:
    return Order.create_market(
        symbol=Symbol.of("BTC", "CRYPTO"),
        side=side,
        quantity=Quantity(Decimal(str(qty))),
        strategy_id="test_strategy",
    )


def _mock_client(
    ping_ok: bool = True,
    order_result: dict | None = None,
    balances: list | None = None,
    symbol_info: dict | None = None,
):
    client = MagicMock()
    if ping_ok:
        client.ping.return_value = {}
    else:
        client.ping.side_effect = Exception("Connection refused")

    client.create_order.return_value = order_result or {
        "orderId": 123456789,
        "status": "FILLED",
        "fills": [
            {"price": "95000.00", "qty": "0.50000", "commission": "0.000050"},
        ],
    }

    client.get_account.return_value = {
        "balances": balances or [
            {"asset": "USDT", "free": "5000.00", "locked": "0.00"},
            {"asset": "BTC", "free": "0.50000", "locked": "0.00"},
        ]
    }

    client.get_symbol_info.return_value = symbol_info or {
        "symbol": "BTCUSDT",
        "filters": [
            {"filterType": "LOT_SIZE", "minQty": "0.00001", "stepSize": "0.00001", "maxQty": "9000.00"},
            {"filterType": "MIN_NOTIONAL", "minNotional": "10.00"},
        ],
    }

    return client


def _make_broker(client=None, testnet: bool = False):
    """Create BinanceBroker with mocked client and fake env credentials."""
    env_patch = {
        "BINANCE_API_KEY": "test_key",
        "BINANCE_SECRET_KEY": "test_secret",
    }
    with patch.dict(os.environ, env_patch):
        with patch("infrastructure.brokers.binance_broker.BinanceBroker._build_client") as mock_build:
            mock_build.return_value = client or _mock_client()
            from infrastructure.brokers.binance_broker import BinanceBroker
            broker = BinanceBroker(testnet=testnet)
            return broker


# ------------------------------------------------------------------ #
# Connectivity                                                         #
# ------------------------------------------------------------------ #

class TestConnectivity:
    @pytest.mark.asyncio
    async def test_is_connected_returns_true_when_ping_succeeds(self):
        broker = _make_broker(_mock_client(ping_ok=True))
        assert await broker.is_connected() is True

    @pytest.mark.asyncio
    async def test_is_connected_returns_false_when_ping_fails(self):
        broker = _make_broker(_mock_client(ping_ok=False))
        assert await broker.is_connected() is False

    @pytest.mark.asyncio
    async def test_is_connected_false_when_client_is_none(self):
        broker = _make_broker()
        broker._client = None
        assert await broker.is_connected() is False


# ------------------------------------------------------------------ #
# Submit order                                                         #
# ------------------------------------------------------------------ #

class TestSubmitOrder:
    @pytest.mark.asyncio
    async def test_market_buy_returns_broker_id(self):
        client = _mock_client(order_result={"orderId": 999, "status": "FILLED", "fills": []})
        broker = _make_broker(client)
        order = _make_order(OrderSide.BUY, qty=0.5)
        broker_id = await broker.submit_order(order)
        assert broker_id == "999"

    @pytest.mark.asyncio
    async def test_market_sell_submits_correctly(self):
        client = _mock_client(order_result={"orderId": 111, "status": "FILLED", "fills": []})
        broker = _make_broker(client)
        order = _make_order(OrderSide.SELL, qty=0.3)
        broker_id = await broker.submit_order(order)
        assert broker_id == "111"
        call_kwargs = client.create_order.call_args[1]
        assert call_kwargs["side"] == "SELL"
        assert call_kwargs["symbol"] == "BTCUSDT"

    @pytest.mark.asyncio
    async def test_limit_order_raises(self):
        broker = _make_broker()
        order = Order.create_limit(
            symbol=Symbol.of("BTC", "CRYPTO"),
            side=OrderSide.BUY,
            quantity=Quantity(Decimal("0.5")),
            limit_price=Price(Decimal("90000")),
        )
        with pytest.raises(ValueError, match="MARKET orders"):
            await broker.submit_order(order)

    @pytest.mark.asyncio
    async def test_unknown_symbol_raises(self):
        broker = _make_broker()
        order = Order.create_market(
            symbol=Symbol.of("DOGE", "CRYPTO"),
            side=OrderSide.BUY,
            quantity=Quantity(Decimal("100")),
        )
        with pytest.raises(ValueError, match="not mapped"):
            await broker.submit_order(order)

    @pytest.mark.asyncio
    async def test_api_error_raises_runtime_error(self):
        client = _mock_client()
        client.create_order.side_effect = Exception("insufficient balance")
        broker = _make_broker(client)
        order = _make_order()
        with pytest.raises(RuntimeError, match="Binance order failed"):
            await broker.submit_order(order)

    @pytest.mark.asyncio
    async def test_quantity_truncated_to_step_size(self):
        client = _mock_client(
            order_result={"orderId": 42, "status": "FILLED", "fills": []},
            symbol_info={
                "symbol": "BTCUSDT",
                "filters": [
                    {"filterType": "LOT_SIZE", "minQty": "0.00001", "stepSize": "0.00001", "maxQty": "9000"},
                ],
            },
        )
        broker = _make_broker(client)
        # 0.123456789 should truncate to 0.12345 (5 decimal places)
        order = _make_order(qty=0.123456789)
        await broker.submit_order(order)
        call_kwargs = client.create_order.call_args[1]
        assert call_kwargs["quantity"] == "0.12345"

    @pytest.mark.asyncio
    async def test_quantity_below_min_lot_raises(self):
        client = _mock_client(
            symbol_info={
                "symbol": "BTCUSDT",
                "filters": [
                    {"filterType": "LOT_SIZE", "minQty": "0.001", "stepSize": "0.001", "maxQty": "9000"},
                ],
            }
        )
        broker = _make_broker(client)
        order = _make_order(qty=0.0001)  # below 0.001 minimum
        with pytest.raises(ValueError, match="minimum lot size"):
            await broker.submit_order(order)


# ------------------------------------------------------------------ #
# Account balance                                                      #
# ------------------------------------------------------------------ #

class TestAccountBalance:
    @pytest.mark.asyncio
    async def test_returns_usdt_free_balance(self):
        client = _mock_client(balances=[
            {"asset": "USDT", "free": "12345.67", "locked": "0.00"},
            {"asset": "BTC", "free": "0.5", "locked": "0.0"},
        ])
        broker = _make_broker(client)
        balance = await broker.get_account_balance()
        assert balance == Decimal("12345.67")

    @pytest.mark.asyncio
    async def test_returns_zero_if_no_usdt(self):
        client = _mock_client(balances=[
            {"asset": "BTC", "free": "1.0", "locked": "0.0"},
        ])
        broker = _make_broker(client)
        balance = await broker.get_account_balance()
        assert balance == Decimal("0")

    @pytest.mark.asyncio
    async def test_api_error_raises(self):
        client = _mock_client()
        client.get_account.side_effect = Exception("API Error")
        broker = _make_broker(client)
        with pytest.raises(RuntimeError, match="Balance fetch failed"):
            await broker.get_account_balance()


# ------------------------------------------------------------------ #
# Positions                                                            #
# ------------------------------------------------------------------ #

class TestGetPositions:
    @pytest.mark.asyncio
    async def test_returns_non_zero_non_usdt_balances(self):
        client = _mock_client(balances=[
            {"asset": "USDT", "free": "5000", "locked": "0"},
            {"asset": "BTC", "free": "0.5", "locked": "0.1"},
            {"asset": "ETH", "free": "0", "locked": "0"},
        ])
        broker = _make_broker(client)
        positions = await broker.get_positions()
        assert "BTC" in positions
        assert positions["BTC"].value == Decimal("0.6")
        assert "ETH" not in positions
        assert "USDT" not in positions

    @pytest.mark.asyncio
    async def test_returns_empty_dict_when_no_positions(self):
        client = _mock_client(balances=[
            {"asset": "USDT", "free": "1000", "locked": "0"},
        ])
        broker = _make_broker(client)
        positions = await broker.get_positions()
        assert positions == {}


# ------------------------------------------------------------------ #
# Cancel order                                                         #
# ------------------------------------------------------------------ #

class TestCancelOrder:
    @pytest.mark.asyncio
    async def test_cancel_market_order_returns_false(self):
        broker = _make_broker()
        result = await broker.cancel_order("99999")
        assert result is False


# ------------------------------------------------------------------ #
# Avg fill price                                                       #
# ------------------------------------------------------------------ #

class TestAvgFillPrice:
    def test_weighted_average_across_fills(self):
        broker = _make_broker()
        result = {
            "fills": [
                {"price": "100.00", "qty": "1.0"},
                {"price": "102.00", "qty": "1.0"},
            ]
        }
        avg = broker._avg_fill_price(result)
        assert avg == Decimal("101.00")

    def test_empty_fills_returns_zero(self):
        broker = _make_broker()
        assert broker._avg_fill_price({"fills": []}) == Decimal("0")
