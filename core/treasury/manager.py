"""
Treasury Manager — manages idle capital when no trading edge exists.

When the system detects no signals for X days (market is sideways without
edge), idle cash is a drag. This module recommends or executes:
1. Move idle cash to stablecoin earn/staking (Binance Simple Earn)
2. Recall cash when signals appear

With €100 this doesn't matter. With €2000+ it's the difference between
0% and 5-10% APY on idle capital.

The actual Binance Earn API integration requires auth — this module
provides the logic and interface. The broker adapter handles execution.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum

logger = logging.getLogger(__name__)


class TreasuryAction(str, Enum):
    HOLD = "HOLD"                    # Do nothing, edge is active
    DEPLOY_TO_EARN = "DEPLOY_TO_EARN"  # Move idle cash to earn
    RECALL_FROM_EARN = "RECALL_FROM_EARN"  # Bring cash back for trading
    PARTIAL_RECALL = "PARTIAL_RECALL"  # Bring some cash back


@dataclass
class TreasuryConfig:
    """Configuration for treasury management."""
    min_idle_days: int = 5              # Days without signals before deploying
    min_deploy_amount: Decimal = Decimal("50")   # Min $ to bother deploying
    max_deploy_pct: float = 80.0        # Max % of cash to deploy (keep reserve)
    reserve_pct: float = 20.0           # Always keep this % liquid for sudden signals
    recall_on_signal: bool = True       # Auto-recall when signal appears
    min_capital_for_treasury: Decimal = Decimal("500")  # Don't bother below this


@dataclass
class TreasuryState:
    """Current treasury state."""
    idle_cash: Decimal = Decimal("0")
    deployed_amount: Decimal = Decimal("0")
    last_signal_timestamp: datetime | None = None
    last_deploy_timestamp: datetime | None = None
    last_recall_timestamp: datetime | None = None
    estimated_apy: float = 0.0


class TreasuryManager:
    """
    Evaluates whether idle cash should be deployed to earn products.

    This is a decision layer — it tells you WHAT to do, not HOW.
    The actual Binance Earn API calls would be in an adapter.

    Usage:
        treasury = TreasuryManager(config)
        action = treasury.evaluate(cash=5000, last_signal_time=..., now=...)
        if action == TreasuryAction.DEPLOY_TO_EARN:
            amount = treasury.recommended_deploy_amount(cash)
            # Call Binance Earn API via adapter
    """

    def __init__(self, config: TreasuryConfig | None = None) -> None:
        self._config = config or TreasuryConfig()
        self._state = TreasuryState()

    @property
    def state(self) -> TreasuryState:
        return self._state

    def record_signal(self, timestamp: datetime | None = None) -> None:
        """Call when any strategy generates a signal (resets idle timer)."""
        self._state.last_signal_timestamp = timestamp or datetime.now(timezone.utc)

    def record_deploy(self, amount: Decimal, timestamp: datetime | None = None) -> None:
        """Call after successfully deploying to earn."""
        self._state.deployed_amount += amount
        self._state.idle_cash -= amount
        self._state.last_deploy_timestamp = timestamp or datetime.now(timezone.utc)
        logger.info("Treasury: deployed $%s to earn. Total deployed: $%s",
                     amount, self._state.deployed_amount)

    def record_recall(self, amount: Decimal, timestamp: datetime | None = None) -> None:
        """Call after successfully recalling from earn."""
        self._state.deployed_amount -= min(amount, self._state.deployed_amount)
        self._state.idle_cash += amount
        self._state.last_recall_timestamp = timestamp or datetime.now(timezone.utc)
        logger.info("Treasury: recalled $%s from earn. Remaining deployed: $%s",
                     amount, self._state.deployed_amount)

    def evaluate(
        self,
        total_cash: Decimal,
        active_positions: int,
        last_signal_time: datetime | None = None,
        now: datetime | None = None,
    ) -> TreasuryAction:
        """
        Evaluate what treasury action to take.

        Args:
            total_cash: Current liquid cash (not including deployed)
            active_positions: Number of open positions
            last_signal_time: When the last trading signal was generated
            now: Current time (for testing)
        """
        now = now or datetime.now(timezone.utc)
        cfg = self._config

        # Update state
        self._state.idle_cash = total_cash
        if last_signal_time:
            self._state.last_signal_timestamp = last_signal_time

        # Don't bother with small accounts
        total_capital = total_cash + self._state.deployed_amount
        if total_capital < cfg.min_capital_for_treasury:
            return TreasuryAction.HOLD

        # If there's a signal and we have deployed capital, recall
        if cfg.recall_on_signal and self._state.deployed_amount > 0:
            if self._state.last_signal_timestamp:
                time_since_signal = now - self._state.last_signal_timestamp
                if time_since_signal < timedelta(days=1):
                    return TreasuryAction.RECALL_FROM_EARN

        # If positions are open, don't deploy more
        if active_positions > 0:
            return TreasuryAction.HOLD

        # Check idle duration
        if self._state.last_signal_timestamp:
            idle_days = (now - self._state.last_signal_timestamp).days
        else:
            idle_days = 0  # No history = don't deploy yet

        if idle_days < cfg.min_idle_days:
            return TreasuryAction.HOLD

        # Check if there's enough to deploy
        deploy_amount = self.recommended_deploy_amount(total_cash)
        if deploy_amount < cfg.min_deploy_amount:
            return TreasuryAction.HOLD

        return TreasuryAction.DEPLOY_TO_EARN

    def recommended_deploy_amount(self, available_cash: Decimal) -> Decimal:
        """How much to deploy, keeping reserve for sudden trading signals."""
        cfg = self._config
        max_deploy = available_cash * Decimal(str(cfg.max_deploy_pct / 100))
        reserve = available_cash * Decimal(str(cfg.reserve_pct / 100))
        deploy = available_cash - reserve
        return min(deploy, max_deploy)

    def summary(self) -> dict:
        return {
            "idle_cash": float(self._state.idle_cash),
            "deployed_amount": float(self._state.deployed_amount),
            "total": float(self._state.idle_cash + self._state.deployed_amount),
            "last_signal": self._state.last_signal_timestamp.isoformat() if self._state.last_signal_timestamp else None,
            "estimated_apy": self._state.estimated_apy,
        }
