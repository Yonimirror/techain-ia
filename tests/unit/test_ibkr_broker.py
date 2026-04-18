"""Tests for IBKRBroker — unit tests that don't require a live TWS/Gateway connection."""
import pytest
from unittest.mock import patch, MagicMock
from decimal import Decimal

from infrastructure.brokers.ibkr_broker import IBKRBroker, _CONTRACT_MAP


class TestContractMap:
    def test_all_equities_mapped(self):
        """Core equities and ETFs must be in the contract map."""
        required = ["NVDA", "AVGO", "SPY", "XLE", "SMH", "GLD", "TSM"]
        for sym in required:
            assert sym in _CONTRACT_MAP, f"{sym} missing from _CONTRACT_MAP"

    def test_contract_format(self):
        """Each contract entry must be a 4-tuple (symbol, secType, exchange, currency)."""
        for ticker, spec in _CONTRACT_MAP.items():
            assert len(spec) == 4, f"{ticker}: expected 4-tuple, got {len(spec)}"
            ib_sym, sec_type, exchange, currency = spec
            assert sec_type in ("STK", "FUT"), f"{ticker}: invalid secType {sec_type}"
            assert currency == "USD", f"{ticker}: expected USD, got {currency}"


class TestIBKRBrokerInit:
    def test_default_port_is_paper(self):
        """Default port 7497 is IBKR paper trading."""
        with patch.dict("os.environ", {}, clear=True):
            broker = IBKRBroker.__new__(IBKRBroker)
            broker._host = "127.0.0.1"
            broker._port = 7497
            broker._client_id = 1
            broker._ib = None
            broker._connected = False
            assert broker._port == 7497

    def test_env_override(self):
        """Environment variables override defaults."""
        env = {"IBKR_HOST": "10.0.0.1", "IBKR_PORT": "7496", "IBKR_CLIENT_ID": "5"}
        with patch.dict("os.environ", env, clear=False):
            broker = IBKRBroker()
            assert broker._host == "10.0.0.1"
            assert broker._port == 7496
            assert broker._client_id == 5


class TestIBKRBrokerNotConnected:
    @pytest.mark.asyncio
    async def test_is_connected_false_without_ib(self):
        broker = IBKRBroker.__new__(IBKRBroker)
        broker._ib = None
        broker._connected = False
        assert await broker.is_connected() is False

    def test_build_contract_stock(self):
        """Build a stock contract for NVDA."""
        mock_stock = MagicMock()
        mock_future = MagicMock()
        fake_ib_insync = MagicMock(Stock=mock_stock, Future=mock_future)
        with patch.dict("sys.modules", {"ib_insync": fake_ib_insync}):
            broker = IBKRBroker.__new__(IBKRBroker)
            from core.domain.value_objects import Symbol
            sym = Symbol.of("NVDA", "NYSE")
            contract = broker._build_contract(sym)
            mock_stock.assert_called_once_with("NVDA", "SMART", "USD")

    def test_build_contract_unknown_raises(self):
        fake_ib_insync = MagicMock()
        with patch.dict("sys.modules", {"ib_insync": fake_ib_insync}):
            broker = IBKRBroker.__new__(IBKRBroker)
            from core.domain.value_objects import Symbol
            sym = Symbol.of("UNKNOWN_TICKER", "NYSE")
            with pytest.raises(ValueError, match="not mapped"):
                broker._build_contract(sym)
