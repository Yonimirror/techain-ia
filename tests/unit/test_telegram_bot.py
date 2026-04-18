"""Tests for Telegram bot command parsing and notifier (Feature 4)."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from apps.telegram_bot.bot import _parse_test_command, TelegramNotifier


class TestParseTestCommand:
    def test_basic_rsi(self):
        result = _parse_test_command("/test RSI 14 oversold 30 ETH 4h 6m")
        assert result is not None
        assert result["strategy"] == "reversion"
        assert result["params"]["rsi_period"] == 14
        assert result["params"]["oversold"] == 30
        assert result["symbol"] == "ETH"
        assert result["timeframe"] == "4h"
        assert result["period_months"] == 6

    def test_ema_with_two_periods(self):
        result = _parse_test_command("/test EMA 9 21 BTC 1d 1y")
        assert result is not None
        assert result["strategy"] == "trend"
        assert result["params"]["ema_fast"] == 9
        assert result["params"]["ema_slow"] == 21
        assert result["period_months"] == 12

    def test_bollinger(self):
        result = _parse_test_command("/test bollinger bb20 std2.0 BTC 4h 6m")
        assert result is not None
        assert result["strategy"] == "bollinger"
        assert result["params"]["bb_period"] == 20
        assert result["params"]["bb_std"] == 2.0

    def test_minimal_command(self):
        result = _parse_test_command("/test RSI 14 BTC")
        assert result is not None
        assert result["strategy"] == "reversion"
        assert result["symbol"] == "BTC"

    def test_invalid_no_strategy(self):
        result = _parse_test_command("/test BTC 4h")
        assert result is None

    def test_not_test_command(self):
        result = _parse_test_command("/status")
        assert result is None

    def test_too_short(self):
        result = _parse_test_command("/test")
        assert result is None

    def test_sol_symbol(self):
        result = _parse_test_command("/test RSI 7 SOL 1h 3m")
        assert result is not None
        assert result["symbol"] == "SOL"
        assert result["timeframe"] == "1h"
        assert result["period_months"] == 3

    def test_default_values(self):
        result = _parse_test_command("/test RSI 14 overbought 70")
        assert result is not None
        assert result["symbol"] == "BTC"  # default
        assert result["timeframe"] == "4h"  # default
        assert result["period_months"] == 6  # default


class TestTelegramNotifier:
    @patch("apps.telegram_bot.bot.BOT_TOKEN", "")
    @patch("apps.telegram_bot.bot.CHAT_ID", "")
    def test_disabled_when_no_token(self):
        notifier = TelegramNotifier(token="", chat_id="")
        assert not notifier.enabled

    def test_enabled_with_credentials(self):
        notifier = TelegramNotifier(token="123:ABC", chat_id="456")
        assert notifier.enabled

    @pytest.mark.asyncio
    @patch("apps.telegram_bot.bot.BOT_TOKEN", "")
    @patch("apps.telegram_bot.bot.CHAT_ID", "")
    async def test_send_returns_false_when_disabled(self):
        notifier = TelegramNotifier(token="", chat_id="")
        result = await notifier.send("test")
        assert result is False

    @pytest.mark.asyncio
    async def test_send_success(self):
        notifier = TelegramNotifier(token="123:ABC", chat_id="456")

        mock_resp = MagicMock()
        mock_resp.status_code = 200

        with patch("httpx.AsyncClient") as mock_client_cls:
            client = AsyncMock()
            client.post.return_value = mock_resp
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = client

            result = await notifier.send("Test message")
            assert result is True

    @pytest.mark.asyncio
    @patch("apps.telegram_bot.bot.BOT_TOKEN", "")
    @patch("apps.telegram_bot.bot.CHAT_ID", "")
    async def test_send_daily_summary(self):
        notifier = TelegramNotifier(token="", chat_id="")
        result = await notifier.send_daily_summary([
            {"hypothesis_id": "test_strat", "symbol": "BTC", "timeframe": "4h",
             "total_return_pct": 2.5, "total_trades": 5, "win_rate_pct": 60.0},
        ])
        assert result is False  # disabled, but no error
