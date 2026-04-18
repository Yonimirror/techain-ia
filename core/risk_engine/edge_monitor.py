"""
Edge Monitor — EWMA-based edge degradation detector.

Tracks profit factor via Exponential Weighted Moving Average (EWMA)
and win rate with a minimum trades gate before acting on any signal.

Why EWMA instead of a fixed rolling window:
  - Gives more weight to recent trades (degradation is detected sooner)
  - Avoids cliff-edge effects where removing one old trade flips the signal
  - More robust to noise than a hard window of N trades

Why a minimum trades gate:
  - With fewer than `min_trades_to_act` closed trades, any metric is
    dominated by sampling noise → no size reduction is applied
  - Default: 15 trades before the monitor influences sizing

Thresholds (configurable in RiskConfig):
    edge_monitor_window:        20  — EWMA span (α = 2 / (window + 1))
    edge_min_win_rate:          0.40
    edge_min_profit_factor:     1.10
    edge_min_trades_to_act:     15  — gate before any reduction applies
"""
from __future__ import annotations
import logging
from decimal import Decimal

logger = logging.getLogger(__name__)


class EdgeMonitor:
    """
    EWMA-based edge health tracker.

    Call record(pnl) after every closed trade.
    Call sizing_factor() from the risk engine to scale position size.
    """

    def __init__(
        self,
        window: int = 20,
        min_win_rate: float = 0.40,
        min_profit_factor: float = 1.10,
        min_trades_to_act: int = 15,
    ) -> None:
        self._window = window
        self._min_win_rate = min_win_rate
        self._min_profit_factor = min_profit_factor
        self._min_trades_to_act = min_trades_to_act

        # EWMA smoothing factor
        self._alpha = 2.0 / (window + 1)

        # State
        self._total_trades: int = 0
        self._ewma_win: float | None = None   # EWMA of is_win (0/1)
        self._ewma_pf: float | None = None    # EWMA of trade P&L ratio proxy
        self._gross_win: float = 0.0
        self._gross_loss: float = 0.0

    def record(self, pnl: Decimal) -> None:
        """Update EWMA state with the result of a closed trade."""
        pnl_f = float(pnl)
        self._total_trades += 1

        is_win = 1.0 if pnl_f > 0 else 0.0

        # EWMA of win flag
        if self._ewma_win is None:
            self._ewma_win = is_win
        else:
            self._ewma_win = self._alpha * is_win + (1 - self._alpha) * self._ewma_win

        # Running gross win/loss for EWMA profit factor proxy
        if pnl_f > 0:
            self._gross_win += pnl_f
        else:
            self._gross_loss += abs(pnl_f)

        # EWMA profit factor: smooth the ratio gross_win/gross_loss
        raw_pf = self._gross_win / self._gross_loss if self._gross_loss > 0 else None
        if raw_pf is not None:
            if self._ewma_pf is None:
                self._ewma_pf = raw_pf
            else:
                self._ewma_pf = self._alpha * raw_pf + (1 - self._alpha) * self._ewma_pf

    # ── Metrics ──────────────────────────────────────────────────────────────

    def win_rate(self) -> float | None:
        """EWMA win rate. None if below minimum trades gate."""
        if self._total_trades < self._min_trades_to_act:
            return None
        return self._ewma_win

    def profit_factor(self) -> float | None:
        """EWMA profit factor. None if below minimum trades gate or no losses yet."""
        if self._total_trades < self._min_trades_to_act:
            return None
        return self._ewma_pf

    def is_degraded(self) -> bool:
        wr = self.win_rate()
        pf = self.profit_factor()
        if wr is None and pf is None:
            return False
        if wr is not None and wr < self._min_win_rate:
            return True
        if pf is not None and pf < self._min_profit_factor:
            return True
        return False

    # ── Sizing ───────────────────────────────────────────────────────────────

    def sizing_factor(self) -> float:
        """
        Returns a multiplier in [0.25, 1.0] to scale position size.

          1.0  — edge healthy, or fewer than min_trades_to_act (no penalty)
          0.25 — worst-case degradation (still trades, just very small)

        Takes the minimum of the win-rate and profit-factor signals
        so both must be healthy to keep full size.
        """
        wr = self.win_rate()
        pf = self.profit_factor()

        if wr is None and pf is None:
            return 1.0

        factor = 1.0

        if wr is not None and wr < self._min_win_rate:
            factor = min(factor, max(0.25, wr / self._min_win_rate))

        if pf is not None and pf < self._min_profit_factor:
            factor = min(factor, max(0.25, pf / self._min_profit_factor))

        if factor < 1.0:
            logger.warning(
                "Edge degradation | trades=%d | ewma_wr=%.3f (min=%.2f) | ewma_pf=%s (min=%.2f) | sizing_factor=%.2f",
                self._total_trades,
                wr if wr is not None else -1.0,
                self._min_win_rate,
                f"{pf:.3f}" if pf is not None else "N/A",
                self._min_profit_factor,
                factor,
            )

        return factor

    def summary(self) -> dict:
        return {
            "trades_tracked": self._total_trades,
            "ewma_win_rate": round(self.win_rate() or 0.0, 3),
            "ewma_profit_factor": round(self.profit_factor() or 0.0, 3),
            "sizing_factor": round(self.sizing_factor(), 3),
            "degraded": self.is_degraded(),
            "gated": self._total_trades < self._min_trades_to_act,
        }
