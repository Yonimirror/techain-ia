"""
Decision tracer — records WHY every trade decision was made.

Creates an immutable audit trail:
Signal → Risk evaluation → Order → Fill

This is critical for debugging, compliance, and strategy improvement.
"""
from __future__ import annotations
import json
import logging
from dataclasses import dataclass, asdict
from datetime import datetime
from uuid import UUID

logger = logging.getLogger(__name__)


@dataclass
class DecisionTrace:
    trace_id: str
    timestamp: str
    symbol: str
    strategy_id: str
    signal_direction: str
    signal_strength: float
    signal_price: float
    risk_outcome: str               # "APPROVED" or "REJECTED"
    risk_rationale: str
    approved_quantity: float | None
    risk_score: float | None
    order_id: str | None
    fill_price: float | None
    fill_quantity: float | None
    fees: float | None
    final_outcome: str              # "FILLED", "REJECTED", "FAILED"


class DecisionTracer:
    """
    Records a full trace of every trading decision.

    Each trace answers: Why was this trade taken (or not taken)?
    What were the exact parameters at decision time?
    """

    def __init__(self, log_to_file: str | None = None) -> None:
        self._traces: list[DecisionTrace] = []
        self._log_file = log_to_file
        self._file_handle = None
        if log_to_file:
            self._file_handle = open(log_to_file, "a", encoding="utf-8")

    def record(self, trace: DecisionTrace) -> None:
        self._traces.append(trace)
        log_data = asdict(trace)
        logger.info("DECISION_TRACE", extra={"trace": log_data})

        if self._file_handle:
            self._file_handle.write(json.dumps(log_data) + "\n")
            self._file_handle.flush()

    def get_traces(self, strategy_id: str | None = None) -> list[DecisionTrace]:
        if strategy_id:
            return [t for t in self._traces if t.strategy_id == strategy_id]
        return list(self._traces)

    def get_approved(self) -> list[DecisionTrace]:
        return [t for t in self._traces if t.risk_outcome == "APPROVED"]

    def get_rejected(self) -> list[DecisionTrace]:
        return [t for t in self._traces if t.risk_outcome == "REJECTED"]

    def rejection_summary(self) -> dict[str, int]:
        from collections import Counter
        return dict(Counter(
            t.risk_rationale for t in self.get_rejected()
        ))

    def close(self) -> None:
        if self._file_handle:
            self._file_handle.close()
