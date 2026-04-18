"""
Strategy Rebalancer — allocates capital proportionally to rolling performance.

Like a fund of funds: strategies that are working get more capital,
strategies in drawdown get less. Rules-based, not discretionary.

Algorithm:
1. Compute rolling Sharpe (or profit factor) per strategy over N recent trades
2. Strategies with positive performance get proportional allocation
3. Strategies with negative performance get minimum allocation (not zero —
   they might recover, and zero means no data to evaluate recovery)
4. Rebalance periodically (e.g., weekly)

The rebalancer outputs allocation weights, not actual orders.
The paper trader / live trader uses these weights as position size multipliers.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

logger = logging.getLogger(__name__)

REBALANCE_DIR = Path("data/rebalancer")


@dataclass
class StrategyPerformance:
    """Rolling performance metrics for one strategy."""
    strategy_id: str
    symbol: str
    timeframe: str
    recent_trades: int = 0
    recent_wins: int = 0
    recent_pnl: Decimal = Decimal("0")
    recent_gross_profit: Decimal = Decimal("0")
    recent_gross_loss: Decimal = Decimal("0")
    max_drawdown_pct: float = 0.0
    last_updated: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def win_rate(self) -> float:
        if self.recent_trades == 0:
            return 0.0
        return self.recent_wins / self.recent_trades

    @property
    def profit_factor(self) -> float:
        if self.recent_gross_loss == 0:
            return float("inf") if self.recent_gross_profit > 0 else 0.0
        return float(self.recent_gross_profit / abs(self.recent_gross_loss))

    @property
    def score(self) -> float:
        """Combined score: profit factor weighted by number of trades."""
        if self.recent_trades < 3:
            return 1.0  # Not enough data — neutral allocation
        pf = min(self.profit_factor, 5.0)  # Cap extreme PFs
        return pf * min(self.win_rate + 0.2, 1.0)  # Slight bonus for any activity


@dataclass
class AllocationResult:
    """Capital allocation weights per strategy."""
    weights: dict[str, float]  # strategy_key → weight (0.0 to 1.0+)
    total_strategies: int
    rebalanced_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    reason: str = ""


class StrategyRebalancer:
    """
    Computes capital allocation weights based on rolling strategy performance.

    Usage:
        rebalancer = StrategyRebalancer()
        rebalancer.update_performance("rsi_btc_4h", trades=[...])
        weights = rebalancer.compute_weights()
        # weights.weights = {"rsi_btc_4h": 1.3, "ema_eth_1d": 0.7, ...}
        # Use as multiplier on position_size_pct
    """

    def __init__(
        self,
        min_weight: float = 0.3,
        max_weight: float = 2.0,
        lookback_trades: int = 20,
        min_trades_for_rebalance: int = 5,
        persist: bool = True,
    ) -> None:
        self._min_weight = min_weight
        self._max_weight = max_weight
        self._lookback = lookback_trades
        self._min_trades = min_trades_for_rebalance
        self._persist = persist
        self._performance: dict[str, StrategyPerformance] = {}
        self._last_weights: AllocationResult | None = None

        if persist:
            self._load()

    def update_performance(
        self,
        strategy_key: str,
        symbol: str,
        timeframe: str,
        closed_trades: list[dict],
    ) -> None:
        """
        Update rolling performance from recent closed trades.

        closed_trades: list of dicts with at least {'pnl': Decimal}
        """
        recent = closed_trades[-self._lookback:]

        wins = sum(1 for t in recent if t.get("pnl", Decimal("0")) > 0)
        total_pnl = sum((t.get("pnl", Decimal("0")) for t in recent), Decimal("0"))
        gross_profit = sum(
            (t["pnl"] for t in recent if t.get("pnl", Decimal("0")) > 0),
            Decimal("0"),
        )
        gross_loss = sum(
            (t["pnl"] for t in recent if t.get("pnl", Decimal("0")) < 0),
            Decimal("0"),
        )

        self._performance[strategy_key] = StrategyPerformance(
            strategy_id=strategy_key,
            symbol=symbol,
            timeframe=timeframe,
            recent_trades=len(recent),
            recent_wins=wins,
            recent_pnl=total_pnl,
            recent_gross_profit=gross_profit,
            recent_gross_loss=gross_loss,
        )

        logger.info(
            "Rebalancer: updated %s | trades=%d | WR=%.1f%% | PF=%.2f",
            strategy_key, len(recent),
            self._performance[strategy_key].win_rate * 100,
            self._performance[strategy_key].profit_factor,
        )
        if self._persist:
            self._save()

    def compute_weights(self) -> AllocationResult:
        """
        Compute allocation weights proportional to performance scores.

        Returns weights normalized so the average is 1.0.
        Strategies performing above average get > 1.0.
        Strategies performing below average get < 1.0 (min_weight floor).
        """
        if not self._performance:
            return AllocationResult(weights={}, total_strategies=0, reason="No performance data")

        # Compute raw scores
        scores: dict[str, float] = {}
        for key, perf in self._performance.items():
            scores[key] = perf.score

        if not scores:
            return AllocationResult(weights={}, total_strategies=0, reason="No scores")

        # Check if we have enough trades across strategies
        total_trades = sum(p.recent_trades for p in self._performance.values())
        if total_trades < self._min_trades:
            # Not enough data — equal weights
            equal = {k: 1.0 for k in scores}
            result = AllocationResult(
                weights=equal,
                total_strategies=len(equal),
                reason=f"Equal weights: only {total_trades} total trades (need {self._min_trades})",
            )
            self._last_weights = result
            return result

        # Normalize: average score = 1.0
        avg_score = sum(scores.values()) / len(scores)
        if avg_score == 0:
            weights = {k: 1.0 for k in scores}
        else:
            weights = {
                k: max(self._min_weight, min(self._max_weight, s / avg_score))
                for k, s in scores.items()
            }

        result = AllocationResult(
            weights=weights,
            total_strategies=len(weights),
            reason=f"Performance-based | avg_score={avg_score:.2f} | trades={total_trades}",
        )
        self._last_weights = result

        for key, w in sorted(weights.items(), key=lambda x: -x[1]):
            perf = self._performance[key]
            logger.info(
                "  %s: weight=%.2f | PF=%.2f | WR=%.0f%% | trades=%d",
                key, w, perf.profit_factor, perf.win_rate * 100, perf.recent_trades,
            )

        if self._persist:
            self._save()
        return result

    @property
    def last_weights(self) -> AllocationResult | None:
        return self._last_weights

    def get_weight(self, strategy_key: str) -> float:
        """Get current weight for a strategy. Returns 1.0 if unknown."""
        if not self._last_weights:
            return 1.0
        return self._last_weights.weights.get(strategy_key, 1.0)

    def summary(self) -> dict:
        return {
            "strategies": {
                k: {
                    "trades": p.recent_trades,
                    "win_rate": f"{p.win_rate:.1%}",
                    "profit_factor": f"{p.profit_factor:.2f}",
                    "pnl": str(p.recent_pnl),
                    "weight": self.get_weight(k),
                }
                for k, p in self._performance.items()
            },
            "total_strategies": len(self._performance),
        }

    def _save(self) -> None:
        REBALANCE_DIR.mkdir(parents=True, exist_ok=True)
        path = REBALANCE_DIR / "performance.json"
        data = {}
        for key, perf in self._performance.items():
            data[key] = {
                "strategy_id": perf.strategy_id,
                "symbol": perf.symbol,
                "timeframe": perf.timeframe,
                "recent_trades": perf.recent_trades,
                "recent_wins": perf.recent_wins,
                "recent_pnl": str(perf.recent_pnl),
                "recent_gross_profit": str(perf.recent_gross_profit),
                "recent_gross_loss": str(perf.recent_gross_loss),
                "last_updated": perf.last_updated.isoformat(),
            }
        if self._last_weights:
            data["__weights__"] = {
                "weights": self._last_weights.weights,
                "total_strategies": self._last_weights.total_strategies,
                "rebalanced_at": self._last_weights.rebalanced_at.isoformat(),
                "reason": self._last_weights.reason,
            }
        with open(path, "w") as f:
            json.dump(data, f, indent=2)

    def _load(self) -> None:
        path = REBALANCE_DIR / "performance.json"
        if not path.exists():
            return
        try:
            with open(path) as f:
                data = json.load(f)
            for key, d in data.items():
                if key == "__weights__":
                    self._last_weights = AllocationResult(
                        weights=d["weights"],
                        total_strategies=d["total_strategies"],
                        rebalanced_at=datetime.fromisoformat(d["rebalanced_at"]),
                        reason=d["reason"],
                    )
                    continue
                self._performance[key] = StrategyPerformance(
                    strategy_id=d["strategy_id"],
                    symbol=d["symbol"],
                    timeframe=d["timeframe"],
                    recent_trades=d["recent_trades"],
                    recent_wins=d["recent_wins"],
                    recent_pnl=Decimal(d["recent_pnl"]),
                    recent_gross_profit=Decimal(d["recent_gross_profit"]),
                    recent_gross_loss=Decimal(d["recent_gross_loss"]),
                    last_updated=datetime.fromisoformat(d["last_updated"]),
                )
            logger.info("Rebalancer loaded %d strategies", len(self._performance))
        except Exception as exc:
            logger.warning("Failed to load rebalancer state: %s", exc)
