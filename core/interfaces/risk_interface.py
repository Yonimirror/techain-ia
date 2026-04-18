from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum

from core.domain.entities import Signal, PortfolioState
from core.domain.value_objects import Quantity


class RejectionReason(str, Enum):
    EXCEEDS_MAX_DRAWDOWN = "EXCEEDS_MAX_DRAWDOWN"
    EXCEEDS_POSITION_LIMIT = "EXCEEDS_POSITION_LIMIT"
    EXCEEDS_EXPOSURE_LIMIT = "EXCEEDS_EXPOSURE_LIMIT"
    KILL_SWITCH_ACTIVE = "KILL_SWITCH_ACTIVE"
    INSUFFICIENT_CAPITAL = "INSUFFICIENT_CAPITAL"
    DUPLICATE_SIGNAL = "DUPLICATE_SIGNAL"
    LOW_SIGNAL_STRENGTH = "LOW_SIGNAL_STRENGTH"
    DAILY_LOSS_LIMIT = "DAILY_LOSS_LIMIT"
    CONSECUTIVE_LOSSES = "CONSECUTIVE_LOSSES"
    SECTOR_CAP_EXCEEDED = "SECTOR_CAP_EXCEEDED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class RiskDecision:
    """Approved trade with computed position size."""
    signal: Signal
    approved_quantity: Quantity
    risk_score: float        # 0.0 (safe) – 1.0 (risky)
    rationale: str = ""


@dataclass(frozen=True)
class RiskRejection:
    """Trade rejected by risk engine."""
    signal: Signal
    reason: RejectionReason
    detail: str = ""


class IRiskEngine(ABC):
    """
    Contract for the risk engine.

    The risk engine is the GATEKEEPER. No trade executes without its approval.
    It computes position sizing and enforces all risk constraints.
    """

    @abstractmethod
    def evaluate(
        self,
        signal: Signal,
        portfolio_state: PortfolioState,
    ) -> RiskDecision | RiskRejection:
        """
        Evaluate a signal against current portfolio state.

        Returns RiskDecision (approved) or RiskRejection (blocked).
        NEVER raises exceptions — always returns one of the two types.
        """
        ...

    @abstractmethod
    def activate_kill_switch(self, reason: str) -> None:
        """Immediately block all new trades."""
        ...

    @abstractmethod
    def deactivate_kill_switch(self) -> None:
        """Resume normal operation (requires explicit action)."""
        ...

    @property
    @abstractmethod
    def kill_switch_active(self) -> bool:
        """Whether the kill switch is currently active."""
        ...
