"""Tests for treasury manager (Feature 5)."""
import pytest
from decimal import Decimal
from datetime import datetime, timedelta, timezone

from core.treasury.manager import (
    TreasuryManager, TreasuryConfig, TreasuryAction,
)


class TestTreasuryBasicRules:
    def test_hold_when_small_account(self):
        """Don't bother with treasury below min_capital."""
        config = TreasuryConfig(min_capital_for_treasury=Decimal("500"))
        manager = TreasuryManager(config)
        action = manager.evaluate(
            total_cash=Decimal("100"),
            active_positions=0,
            last_signal_time=datetime.now(timezone.utc) - timedelta(days=30),
        )
        assert action == TreasuryAction.HOLD

    def test_hold_when_positions_open(self):
        """Don't deploy while positions are active."""
        config = TreasuryConfig(min_capital_for_treasury=Decimal("100"))
        manager = TreasuryManager(config)
        action = manager.evaluate(
            total_cash=Decimal("2000"),
            active_positions=2,
            last_signal_time=datetime.now(timezone.utc) - timedelta(days=30),
        )
        assert action == TreasuryAction.HOLD

    def test_hold_when_recent_signals(self):
        """Don't deploy if signals were recent (< min_idle_days)."""
        config = TreasuryConfig(
            min_idle_days=5,
            min_capital_for_treasury=Decimal("100"),
        )
        manager = TreasuryManager(config)
        action = manager.evaluate(
            total_cash=Decimal("2000"),
            active_positions=0,
            last_signal_time=datetime.now(timezone.utc) - timedelta(days=2),
        )
        assert action == TreasuryAction.HOLD

    def test_deploy_when_idle(self):
        """Deploy to earn after min_idle_days with no signals."""
        config = TreasuryConfig(
            min_idle_days=5,
            min_capital_for_treasury=Decimal("100"),
            min_deploy_amount=Decimal("50"),
        )
        manager = TreasuryManager(config)
        action = manager.evaluate(
            total_cash=Decimal("2000"),
            active_positions=0,
            last_signal_time=datetime.now(timezone.utc) - timedelta(days=10),
        )
        assert action == TreasuryAction.DEPLOY_TO_EARN


class TestTreasuryRecall:
    def test_recall_on_fresh_signal(self):
        """Recall deployed capital when signal appears."""
        config = TreasuryConfig(
            recall_on_signal=True,
            min_capital_for_treasury=Decimal("100"),
        )
        manager = TreasuryManager(config)
        # Simulate having deployed
        manager.record_deploy(Decimal("1000"))
        action = manager.evaluate(
            total_cash=Decimal("500"),
            active_positions=0,
            last_signal_time=datetime.now(timezone.utc),  # just now
        )
        assert action == TreasuryAction.RECALL_FROM_EARN

    def test_no_recall_when_nothing_deployed(self):
        config = TreasuryConfig(min_capital_for_treasury=Decimal("100"))
        manager = TreasuryManager(config)
        action = manager.evaluate(
            total_cash=Decimal("2000"),
            active_positions=0,
            last_signal_time=datetime.now(timezone.utc),
        )
        # Nothing deployed, so no recall — but also too recent to deploy
        assert action == TreasuryAction.HOLD


class TestDeployAmount:
    def test_respects_reserve(self):
        config = TreasuryConfig(
            max_deploy_pct=80.0,
            reserve_pct=20.0,
        )
        manager = TreasuryManager(config)
        amount = manager.recommended_deploy_amount(Decimal("1000"))
        # 80% max, 20% reserve → deploy 80% = $800
        assert amount == Decimal("800.0")

    def test_small_amount_rejected(self):
        config = TreasuryConfig(
            min_deploy_amount=Decimal("50"),
            min_idle_days=1,
            min_capital_for_treasury=Decimal("10"),
        )
        manager = TreasuryManager(config)
        action = manager.evaluate(
            total_cash=Decimal("40"),  # deploy amount would be ~$32
            active_positions=0,
            last_signal_time=datetime.now(timezone.utc) - timedelta(days=10),
        )
        assert action == TreasuryAction.HOLD  # Too small to deploy


class TestTreasuryState:
    def test_deploy_updates_state(self):
        manager = TreasuryManager()
        manager._state.idle_cash = Decimal("2000")
        manager.record_deploy(Decimal("500"))
        assert manager.state.deployed_amount == Decimal("500")
        assert manager.state.idle_cash == Decimal("1500")

    def test_recall_updates_state(self):
        manager = TreasuryManager()
        manager._state.deployed_amount = Decimal("500")
        manager._state.idle_cash = Decimal("1500")
        manager.record_recall(Decimal("500"))
        assert manager.state.deployed_amount == Decimal("0")
        assert manager.state.idle_cash == Decimal("2000")

    def test_signal_recording(self):
        manager = TreasuryManager()
        now = datetime.now(timezone.utc)
        manager.record_signal(now)
        assert manager.state.last_signal_timestamp == now

    def test_summary(self):
        manager = TreasuryManager()
        manager._state.idle_cash = Decimal("1000")
        manager._state.deployed_amount = Decimal("500")
        s = manager.summary()
        assert s["total"] == 1500.0
        assert s["idle_cash"] == 1000.0
        assert s["deployed_amount"] == 500.0
