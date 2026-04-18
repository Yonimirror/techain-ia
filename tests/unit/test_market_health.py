"""Tests for market health detector (Feature 3)."""
import pytest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch, MagicMock
import httpx

from core.market_health.detector import (
    MarketHealthDetector,
    MarketHealthStatus,
    HealthLevel,
    HealthThresholds,
)


def _mock_response(status_code: int, json_data):
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = json_data
    return resp


class TestHealthLevel:
    def test_healthy_sizing_factor(self):
        status = MarketHealthStatus(
            level=HealthLevel.HEALTHY,
            spread_bps=2.0,
            volume_24h_usd=1_000_000_000,
            funding_rate=0.0001,
        )
        assert status.sizing_factor == 1.0

    def test_degraded_sizing_factor(self):
        status = MarketHealthStatus(
            level=HealthLevel.DEGRADED,
            spread_bps=20.0,
            volume_24h_usd=30_000_000,
            funding_rate=0.0005,
            issues=["High spread: 20.0 bps"],
        )
        assert status.sizing_factor == 0.5

    def test_critical_sizing_factor(self):
        status = MarketHealthStatus(
            level=HealthLevel.CRITICAL,
            spread_bps=60.0,
            volume_24h_usd=5_000_000,
            funding_rate=0.002,
            issues=["CRITICAL spread: 60.0 bps"],
        )
        assert status.sizing_factor == 0.0


class TestThresholds:
    @pytest.mark.asyncio
    async def test_healthy_market(self):
        detector = MarketHealthDetector()

        async def mock_get(url, **kwargs):
            if "bookTicker" in url:
                return _mock_response(200, {"bidPrice": "50000.00", "askPrice": "50001.00"})
            elif "24hr" in url:
                return _mock_response(200, {"quoteVolume": "2000000000"})
            elif "fundingRate" in url:
                return _mock_response(200, [{"fundingRate": "0.0001"}])
            return _mock_response(404, {})

        with patch("core.market_health.detector.httpx.AsyncClient") as mock_client:
            client_instance = AsyncMock()
            client_instance.get.side_effect = mock_get
            client_instance.__aenter__ = AsyncMock(return_value=client_instance)
            client_instance.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value = client_instance

            status = await detector.check("BTC")
            assert status.level == HealthLevel.HEALTHY
            assert status.sizing_factor == 1.0
            assert len(status.issues) == 0

    @pytest.mark.asyncio
    async def test_high_spread_degraded(self):
        detector = MarketHealthDetector(HealthThresholds(max_spread_bps=10.0))

        async def mock_get(url, **kwargs):
            if "bookTicker" in url:
                # Spread = (50020-50000)/50010 * 10000 ≈ 4 bps → set wider
                return _mock_response(200, {"bidPrice": "50000.00", "askPrice": "50100.00"})
            elif "24hr" in url:
                return _mock_response(200, {"quoteVolume": "2000000000"})
            elif "fundingRate" in url:
                return _mock_response(200, [{"fundingRate": "0.0001"}])
            return _mock_response(404, {})

        with patch("core.market_health.detector.httpx.AsyncClient") as mock_client:
            client_instance = AsyncMock()
            client_instance.get.side_effect = mock_get
            client_instance.__aenter__ = AsyncMock(return_value=client_instance)
            client_instance.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value = client_instance

            status = await detector.check("BTC")
            assert status.level == HealthLevel.DEGRADED
            assert any("spread" in i.lower() for i in status.issues)

    @pytest.mark.asyncio
    async def test_low_volume_degraded(self):
        detector = MarketHealthDetector()

        async def mock_get(url, **kwargs):
            if "bookTicker" in url:
                return _mock_response(200, {"bidPrice": "50000.00", "askPrice": "50001.00"})
            elif "24hr" in url:
                return _mock_response(200, {"quoteVolume": "30000000"})  # $30M < $50M
            elif "fundingRate" in url:
                return _mock_response(200, [{"fundingRate": "0.0001"}])
            return _mock_response(404, {})

        with patch("core.market_health.detector.httpx.AsyncClient") as mock_client:
            client_instance = AsyncMock()
            client_instance.get.side_effect = mock_get
            client_instance.__aenter__ = AsyncMock(return_value=client_instance)
            client_instance.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value = client_instance

            status = await detector.check("BTC")
            assert status.level == HealthLevel.DEGRADED
            assert any("volume" in i.lower() for i in status.issues)

    @pytest.mark.asyncio
    async def test_extreme_funding_degraded(self):
        detector = MarketHealthDetector()

        async def mock_get(url, **kwargs):
            if "bookTicker" in url:
                return _mock_response(200, {"bidPrice": "50000.00", "askPrice": "50001.00"})
            elif "24hr" in url:
                return _mock_response(200, {"quoteVolume": "2000000000"})
            elif "fundingRate" in url:
                return _mock_response(200, [{"fundingRate": "0.005"}])  # 0.5% > 0.1%
            return _mock_response(404, {})

        with patch("core.market_health.detector.httpx.AsyncClient") as mock_client:
            client_instance = AsyncMock()
            client_instance.get.side_effect = mock_get
            client_instance.__aenter__ = AsyncMock(return_value=client_instance)
            client_instance.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value = client_instance

            status = await detector.check("BTC")
            assert status.level == HealthLevel.DEGRADED
            assert any("funding" in i.lower() for i in status.issues)

    @pytest.mark.asyncio
    async def test_critical_spread(self):
        detector = MarketHealthDetector()

        async def mock_get(url, **kwargs):
            if "bookTicker" in url:
                return _mock_response(200, {"bidPrice": "50000.00", "askPrice": "50300.00"})
            elif "24hr" in url:
                return _mock_response(200, {"quoteVolume": "2000000000"})
            elif "fundingRate" in url:
                return _mock_response(200, [{"fundingRate": "0.0001"}])
            return _mock_response(404, {})

        with patch("core.market_health.detector.httpx.AsyncClient") as mock_client:
            client_instance = AsyncMock()
            client_instance.get.side_effect = mock_get
            client_instance.__aenter__ = AsyncMock(return_value=client_instance)
            client_instance.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value = client_instance

            status = await detector.check("BTC")
            assert status.level == HealthLevel.CRITICAL
            assert status.sizing_factor == 0.0


class TestCaching:
    @pytest.mark.asyncio
    async def test_cached_result_returned(self):
        detector = MarketHealthDetector(cache_seconds=300)
        # Manually populate cache
        cached = MarketHealthStatus(
            level=HealthLevel.HEALTHY,
            spread_bps=1.0,
            volume_24h_usd=1e9,
            funding_rate=0.0001,
            checked_at=datetime.now(timezone.utc),
        )
        detector._cache["BTC"] = cached

        result = await detector.check("BTC")
        assert result is cached  # Same object, no API call

    @pytest.mark.asyncio
    async def test_expired_cache_refreshed(self):
        detector = MarketHealthDetector(cache_seconds=60)
        old = MarketHealthStatus(
            level=HealthLevel.HEALTHY,
            spread_bps=1.0,
            volume_24h_usd=1e9,
            funding_rate=0.0001,
            checked_at=datetime.now(timezone.utc) - timedelta(seconds=120),
        )
        detector._cache["BTC"] = old

        async def mock_get(url, **kwargs):
            if "bookTicker" in url:
                return _mock_response(200, {"bidPrice": "50000.00", "askPrice": "50001.00"})
            elif "24hr" in url:
                return _mock_response(200, {"quoteVolume": "2000000000"})
            elif "fundingRate" in url:
                return _mock_response(200, [{"fundingRate": "0.0001"}])
            return _mock_response(404, {})

        with patch("core.market_health.detector.httpx.AsyncClient") as mock_client:
            client_instance = AsyncMock()
            client_instance.get.side_effect = mock_get
            client_instance.__aenter__ = AsyncMock(return_value=client_instance)
            client_instance.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value = client_instance

            result = await detector.check("BTC")
            assert result is not old

    def test_invalidate_cache_single(self):
        detector = MarketHealthDetector()
        detector._cache["BTC"] = MagicMock()
        detector._cache["ETH"] = MagicMock()
        detector.invalidate_cache("BTC")
        assert "BTC" not in detector._cache
        assert "ETH" in detector._cache

    def test_invalidate_cache_all(self):
        detector = MarketHealthDetector()
        detector._cache["BTC"] = MagicMock()
        detector._cache["ETH"] = MagicMock()
        detector.invalidate_cache()
        assert len(detector._cache) == 0


class TestFailOpen:
    @pytest.mark.asyncio
    async def test_api_failure_returns_healthy(self):
        """If API calls fail, detector should return HEALTHY (fail-open for paper trading)."""
        detector = MarketHealthDetector()

        async def mock_get(url, **kwargs):
            raise httpx.ConnectError("Connection refused")

        with patch("core.market_health.detector.httpx.AsyncClient") as mock_client:
            client_instance = AsyncMock()
            client_instance.get.side_effect = mock_get
            client_instance.__aenter__ = AsyncMock(return_value=client_instance)
            client_instance.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value = client_instance

            status = await detector.check("BTC")
            # All values are 0 defaults → no thresholds crossed → HEALTHY
            assert status.level == HealthLevel.HEALTHY
