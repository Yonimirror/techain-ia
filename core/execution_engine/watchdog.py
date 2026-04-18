"""
Execution Watchdog — compares real fills against simulated fills.

When trading live, the system runs both:
1. Real broker fill (actual execution)
2. Paper broker simulation (what the backtest assumed)

If the real fill consistently costs more than simulated,
the edge is being eroded by slippage. The watchdog tracks this
and triggers alerts when divergence exceeds thresholds.

This is the most important metric when moving from paper to real.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

logger = logging.getLogger(__name__)

WATCHDOG_DIR = Path("data/watchdog")


@dataclass
class FillComparison:
    """Single comparison between real and simulated fill."""
    order_id: str
    symbol: str
    side: str
    quantity: Decimal
    real_fill_price: Decimal
    simulated_fill_price: Decimal
    real_fees: Decimal
    simulated_fees: Decimal
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def price_slippage_bps(self) -> float:
        """Slippage in basis points: positive = paid more than expected."""
        if self.simulated_fill_price == 0:
            return 0.0
        diff = self.real_fill_price - self.simulated_fill_price
        # For BUY: positive diff = paid more = bad
        # For SELL: negative diff = received less = bad
        if self.side == "SELL":
            diff = -diff
        return float(diff / self.simulated_fill_price * 10000)

    @property
    def total_cost_diff(self) -> Decimal:
        """Total cost difference (price slippage + fee difference)."""
        notional_real = self.real_fill_price * self.quantity
        notional_sim = self.simulated_fill_price * self.quantity
        if self.side == "BUY":
            return (notional_real + self.real_fees) - (notional_sim + self.simulated_fees)
        else:
            return (notional_sim - self.simulated_fees) - (notional_real - self.real_fees)

    def to_dict(self) -> dict:
        return {
            "order_id": self.order_id,
            "symbol": self.symbol,
            "side": self.side,
            "quantity": str(self.quantity),
            "real_fill_price": str(self.real_fill_price),
            "simulated_fill_price": str(self.simulated_fill_price),
            "real_fees": str(self.real_fees),
            "simulated_fees": str(self.simulated_fees),
            "price_slippage_bps": self.price_slippage_bps,
            "total_cost_diff": str(self.total_cost_diff),
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class WatchdogReport:
    """Aggregated slippage statistics."""
    total_fills: int
    mean_slippage_bps: float
    median_slippage_bps: float
    max_slippage_bps: float
    p95_slippage_bps: float
    total_cost_leaked: Decimal
    alert_triggered: bool
    alert_reason: str = ""


class ExecutionWatchdog:
    """
    Tracks real vs simulated fills and alerts on divergence.

    Usage:
        watchdog = ExecutionWatchdog(alert_threshold_bps=15.0)

        # After every real fill:
        watchdog.record(comparison)

        # Periodically check:
        report = watchdog.report()
        if report.alert_triggered:
            # Edge is being eaten by slippage!
    """

    def __init__(
        self,
        alert_threshold_bps: float = 15.0,
        min_fills_for_alert: int = 10,
        persist: bool = True,
    ) -> None:
        self._threshold_bps = alert_threshold_bps
        self._min_fills = min_fills_for_alert
        self._persist = persist
        self._comparisons: list[FillComparison] = []

        if persist:
            self._load()

    def record(self, comparison: FillComparison) -> None:
        """Record a new fill comparison."""
        self._comparisons.append(comparison)
        logger.info(
            "Watchdog: %s %s | slippage=%.1f bps | cost_diff=%s",
            comparison.side, comparison.symbol,
            comparison.price_slippage_bps, comparison.total_cost_diff,
        )
        if self._persist:
            self._save()

    def record_fill(
        self,
        order_id: str,
        symbol: str,
        side: str,
        quantity: Decimal,
        real_price: Decimal,
        simulated_price: Decimal,
        real_fees: Decimal,
        simulated_fees: Decimal,
    ) -> FillComparison:
        """Convenience method to create and record a comparison."""
        comp = FillComparison(
            order_id=order_id,
            symbol=symbol,
            side=side,
            quantity=quantity,
            real_fill_price=real_price,
            simulated_fill_price=simulated_price,
            real_fees=real_fees,
            simulated_fees=simulated_fees,
        )
        self.record(comp)
        return comp

    def report(self, last_n: int | None = None) -> WatchdogReport:
        """Generate aggregated slippage report."""
        data = self._comparisons[-last_n:] if last_n else self._comparisons

        if not data:
            return WatchdogReport(
                total_fills=0,
                mean_slippage_bps=0.0,
                median_slippage_bps=0.0,
                max_slippage_bps=0.0,
                p95_slippage_bps=0.0,
                total_cost_leaked=Decimal("0"),
                alert_triggered=False,
            )

        slippages = [c.price_slippage_bps for c in data]
        slippages.sort()
        n = len(slippages)

        mean_slip = sum(slippages) / n
        median_slip = slippages[n // 2]
        max_slip = max(slippages)
        p95_idx = min(int(n * 0.95), n - 1)
        p95_slip = slippages[p95_idx]
        total_leaked = sum((c.total_cost_diff for c in data), Decimal("0"))

        # Alert logic
        alert = False
        reason = ""
        if n >= self._min_fills:
            if mean_slip > self._threshold_bps:
                alert = True
                reason = (
                    f"Mean slippage {mean_slip:.1f} bps exceeds threshold "
                    f"{self._threshold_bps:.1f} bps over {n} fills. "
                    f"Total cost leaked: ${total_leaked:.2f}"
                )
            elif p95_slip > self._threshold_bps * 3:
                alert = True
                reason = (
                    f"P95 slippage {p95_slip:.1f} bps is extreme "
                    f"(3x threshold). Tail risk in execution."
                )

        if alert:
            logger.warning("WATCHDOG ALERT: %s", reason)

        return WatchdogReport(
            total_fills=n,
            mean_slippage_bps=mean_slip,
            median_slippage_bps=median_slip,
            max_slippage_bps=max_slip,
            p95_slippage_bps=p95_slip,
            total_cost_leaked=total_leaked,
            alert_triggered=alert,
            alert_reason=reason,
        )

    def _save(self) -> None:
        WATCHDOG_DIR.mkdir(parents=True, exist_ok=True)
        path = WATCHDOG_DIR / "fill_comparisons.json"
        data = [c.to_dict() for c in self._comparisons]
        with open(path, "w") as f:
            json.dump(data, f, indent=2)

    def _load(self) -> None:
        path = WATCHDOG_DIR / "fill_comparisons.json"
        if not path.exists():
            return
        try:
            with open(path) as f:
                data = json.load(f)
            for d in data:
                self._comparisons.append(FillComparison(
                    order_id=d["order_id"],
                    symbol=d["symbol"],
                    side=d["side"],
                    quantity=Decimal(d["quantity"]),
                    real_fill_price=Decimal(d["real_fill_price"]),
                    simulated_fill_price=Decimal(d["simulated_fill_price"]),
                    real_fees=Decimal(d["real_fees"]),
                    simulated_fees=Decimal(d["simulated_fees"]),
                    timestamp=datetime.fromisoformat(d["timestamp"]),
                ))
            logger.info("Watchdog loaded %d historical comparisons", len(self._comparisons))
        except Exception as exc:
            logger.warning("Failed to load watchdog history: %s", exc)
