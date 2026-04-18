"""
Metrics collection.

Tracks key trading system metrics in memory.
In production, these can be exported to Prometheus/Grafana.
"""
from __future__ import annotations
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import DefaultDict


@dataclass
class TradeMetrics:
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    total_pnl: Decimal = Decimal("0")
    max_drawdown_pct: float = 0.0
    current_equity: Decimal = Decimal("0")
    peak_equity: Decimal = Decimal("0")
    last_trade_at: datetime | None = None


class MetricsCollector:
    """
    Thread-safe in-memory metrics collector.

    Tracks:
    - PnL (realized and unrealized)
    - Drawdown
    - Trade counts and win rate
    - Signals per strategy
    - Risk rejections per reason
    - Event counts
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._trade_metrics = TradeMetrics()
        self._signals_by_strategy: DefaultDict[str, int] = defaultdict(int)
        self._rejections_by_reason: DefaultDict[str, int] = defaultdict(int)
        self._event_counts: DefaultDict[str, int] = defaultdict(int)
        self._equity_snapshots: list[tuple[datetime, Decimal]] = []

    def record_trade(self, pnl: Decimal, won: bool) -> None:
        with self._lock:
            m = self._trade_metrics
            m.total_trades += 1
            m.total_pnl += pnl
            if won:
                m.winning_trades += 1
            else:
                m.losing_trades += 1
            m.last_trade_at = datetime.now(timezone.utc)

    def record_signal(self, strategy_id: str) -> None:
        with self._lock:
            self._signals_by_strategy[strategy_id] += 1

    def record_rejection(self, reason: str) -> None:
        with self._lock:
            self._rejections_by_reason[reason] += 1

    def record_event(self, event_type: str) -> None:
        with self._lock:
            self._event_counts[event_type] += 1

    def update_equity(self, equity: Decimal) -> None:
        with self._lock:
            m = self._trade_metrics
            m.current_equity = equity
            if equity > m.peak_equity:
                m.peak_equity = equity
            if m.peak_equity > 0:
                dd = float((m.peak_equity - equity) / m.peak_equity * 100)
                if dd > m.max_drawdown_pct:
                    m.max_drawdown_pct = dd
            self._equity_snapshots.append((datetime.now(timezone.utc), equity))

    def snapshot(self) -> dict:
        with self._lock:
            m = self._trade_metrics
            win_rate = (
                m.winning_trades / m.total_trades * 100
                if m.total_trades > 0 else 0.0
            )
            return {
                "trades": {
                    "total": m.total_trades,
                    "wins": m.winning_trades,
                    "losses": m.losing_trades,
                    "win_rate_pct": round(win_rate, 2),
                    "total_pnl": float(m.total_pnl),
                    "last_trade_at": m.last_trade_at.isoformat() if m.last_trade_at else None,
                },
                "portfolio": {
                    "current_equity": float(m.current_equity),
                    "peak_equity": float(m.peak_equity),
                    "max_drawdown_pct": round(m.max_drawdown_pct, 4),
                },
                "signals_by_strategy": dict(self._signals_by_strategy),
                "rejections_by_reason": dict(self._rejections_by_reason),
                "event_counts": dict(self._event_counts),
            }

    def reset(self) -> None:
        with self._lock:
            self._trade_metrics = TradeMetrics()
            self._signals_by_strategy.clear()
            self._rejections_by_reason.clear()
            self._event_counts.clear()
            self._equity_snapshots.clear()
