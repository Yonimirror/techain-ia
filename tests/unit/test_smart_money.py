"""Tests para el módulo Smart Money."""
import asyncio
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from infrastructure.smart_money.whale_alert_provider import WhaleAlertProvider, WhaleTransaction
from infrastructure.smart_money.order_flow_provider import OrderFlowProvider, OrderFlowSignal
from infrastructure.smart_money.smart_money_aggregator import SmartMoneyAggregator, SmartMoneyBias


# ── WhaleTransaction tests ──────────────────────────────────────────────────

class TestWhaleTransaction:
    def _make_tx(self, from_type, to_type) -> WhaleTransaction:
        return WhaleTransaction(
            id="1", symbol="BTC", blockchain="bitcoin",
            amount=500, amount_usd=41_500_000,
            from_type=from_type, to_type=to_type,
            from_name="Binance", to_name="Unknown",
            timestamp=datetime.now(timezone.utc), hash="abc123",
        )

    def test_inflow_is_bearish(self):
        tx = self._make_tx(from_type="wallet", to_type="exchange")
        assert tx.direction == "inflow"
        assert tx.is_bearish
        assert not tx.is_bullish

    def test_outflow_is_bullish(self):
        tx = self._make_tx(from_type="exchange", to_type="wallet")
        assert tx.direction == "outflow"
        assert tx.is_bullish
        assert not tx.is_bearish

    def test_transfer_is_neutral(self):
        tx = self._make_tx(from_type="wallet", to_type="wallet")
        assert tx.direction == "transfer"
        assert not tx.is_bullish
        assert not tx.is_bearish


# ── WhaleAlertProvider tests ────────────────────────────────────────────────

class TestWhaleAlertProvider:
    def test_simulated_transactions_without_api_key(self):
        provider = WhaleAlertProvider(api_key="")
        txs = provider._simulated_transactions("BTC")
        assert len(txs) >= 3
        assert all(t.symbol == "BTC" for t in txs)

    @pytest.mark.asyncio
    async def test_get_net_flow_without_api_key(self):
        provider = WhaleAlertProvider(api_key="")
        flow = await provider.get_net_flow("BTC")
        assert "bias" in flow
        assert flow["bias"] in ("bullish", "bearish", "neutral")
        assert "net_flow_usd" in flow

    @pytest.mark.asyncio
    async def test_net_flow_bullish_when_outflows_dominate(self):
        provider = WhaleAlertProvider(api_key="")
        # Mock transacciones con outflows dominantes
        provider.get_recent_transactions = AsyncMock(return_value=[
            WhaleTransaction("1", "BTC", "bitcoin", 1000, 83_000_000,
                           "exchange", "wallet", "Binance", "Cold Wallet",
                           datetime.now(timezone.utc), "hash1"),
            WhaleTransaction("2", "BTC", "bitcoin", 500, 41_500_000,
                           "exchange", "wallet", "Coinbase", "Cold Wallet",
                           datetime.now(timezone.utc), "hash2"),
        ])
        flow = await provider.get_net_flow("BTC")
        assert flow["bias"] == "bullish"
        assert flow["net_flow_usd"] > 0


# ── OrderFlowSignal tests ───────────────────────────────────────────────────

class TestOrderFlowSignal:
    def _make_signal(self, bid_ask_imbalance, volume_ratio) -> OrderFlowSignal:
        return OrderFlowSignal(
            symbol="BTC",
            timestamp=datetime.now(timezone.utc),
            buy_volume=volume_ratio * 100,
            sell_volume=100,
            volume_ratio=volume_ratio,
            best_bid=83000, best_ask=83010, spread_bps=1.2,
            large_bids_usd=bid_ask_imbalance * 10_000_000 + 5_000_000,
            large_asks_usd=(1 - bid_ask_imbalance) * 10_000_000 + 5_000_000,
            bid_ask_imbalance=bid_ask_imbalance,
        )

    def test_bullish_when_buyers_dominate(self):
        signal = self._make_signal(bid_ask_imbalance=0.5, volume_ratio=1.5)
        assert signal.is_bullish

    def test_bearish_when_sellers_dominate(self):
        signal = self._make_signal(bid_ask_imbalance=-0.5, volume_ratio=0.6)
        assert signal.is_bearish

    def test_neutral_when_balanced(self):
        signal = self._make_signal(bid_ask_imbalance=0.1, volume_ratio=1.0)
        assert signal.bias == "neutral"


# ── SmartMoneyAggregator tests ──────────────────────────────────────────────

class TestSmartMoneyAggregator:
    def _make_aggregator(self, whale_bias, of_bias):
        whale = MagicMock()
        whale.get_net_flow = AsyncMock(return_value={
            "bias": whale_bias,
            "net_flow_usd": 10_000_000 if whale_bias == "bullish" else -10_000_000,
            "inflow_usd": 0, "outflow_usd": 10_000_000,
            "transaction_count": 5, "symbol": "BTC", "lookback_hours": 24,
        })

        of = MagicMock()
        vol_ratio = 1.5 if of_bias == "bullish" else (0.6 if of_bias == "bearish" else 1.0)
        imbalance = 0.4 if of_bias == "bullish" else (-0.4 if of_bias == "bearish" else 0.0)
        of_signal = OrderFlowSignal(
            symbol="BTC", timestamp=datetime.now(timezone.utc),
            buy_volume=vol_ratio * 100, sell_volume=100, volume_ratio=vol_ratio,
            best_bid=83000, best_ask=83010, spread_bps=1.2,
            large_bids_usd=5_000_000 if of_bias != "bearish" else 1_000_000,
            large_asks_usd=3_000_000 if of_bias != "bearish" else 7_000_000,
            bid_ask_imbalance=imbalance,
        )
        of.get_signal = AsyncMock(return_value=of_signal)

        return SmartMoneyAggregator(whale_provider=whale, orderflow_provider=of)

    @pytest.mark.asyncio
    async def test_both_bullish_gives_high_conviction(self):
        agg = self._make_aggregator("bullish", "bullish")
        signal = await agg.get_signal("BTC")
        assert signal.bias == SmartMoneyBias.BULLISH
        assert signal.conviction >= 0.8

    @pytest.mark.asyncio
    async def test_both_bearish_gives_bearish_signal(self):
        agg = self._make_aggregator("bearish", "bearish")
        signal = await agg.get_signal("BTC")
        assert signal.bias == SmartMoneyBias.BEARISH
        assert signal.should_skip

    @pytest.mark.asyncio
    async def test_conflict_gives_neutral(self):
        agg = self._make_aggregator("bullish", "bearish")
        signal = await agg.get_signal("BTC")
        assert signal.bias == SmartMoneyBias.NEUTRAL
        assert signal.conviction < 0.5

    @pytest.mark.asyncio
    async def test_position_size_multiplier_bullish_high_conviction(self):
        agg = self._make_aggregator("bullish", "bullish")
        signal = await agg.get_signal("BTC")
        assert signal.position_size_multiplier == 1.5

    @pytest.mark.asyncio
    async def test_position_size_multiplier_bearish_is_zero(self):
        agg = self._make_aggregator("bearish", "bearish")
        signal = await agg.get_signal("BTC")
        assert signal.position_size_multiplier == 0.0
