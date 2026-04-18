from __future__ import annotations
import logging
from datetime import date, datetime, timezone
from decimal import Decimal

from core.domain.entities import Signal, PortfolioState
from core.domain.entities.signal import SignalDirection
from core.domain.entities.order import OrderSide
from core.domain.value_objects import Quantity
from core.interfaces.risk_interface import (
    IRiskEngine, RiskDecision, RiskRejection, RejectionReason,
)
from core.risk_engine.config import RiskConfig
from core.risk_engine.edge_monitor import EdgeMonitor
from core.risk_engine.position_sizer import compute_position_size
from core.risk_engine.sector_caps import SectorCapManager

logger = logging.getLogger(__name__)


class RiskEngine(IRiskEngine):
    """
    Production risk engine.

    Evaluation pipeline (in order):
    1. Kill switch check
    2. Signal strength filter
    3. Max drawdown check
    4. Daily loss limit check
    5. Exposure limit check
    6. Position limit per symbol check
    7. Capital sufficiency check
    8. Compute position size
    9. Approve
    """

    def __init__(self, config: RiskConfig, sector_caps: SectorCapManager | None = None) -> None:
        self._config = config
        self._sector_caps = sector_caps
        self._kill_switch_active: bool = False
        self._kill_switch_reason: str = ""
        self._trades_today: int = 0
        self._daily_loss: Decimal = Decimal("0")
        self._equity_at_day_start: Decimal = Decimal("0")
        self._consecutive_losses: int = 0
        self._last_reset_date: date = datetime.now(timezone.utc).date()
        self._edge_monitor = EdgeMonitor(
            window=config.edge_monitor_window,
            min_win_rate=config.edge_min_win_rate,
            min_profit_factor=config.edge_min_profit_factor,
            min_trades_to_act=config.edge_min_trades_to_act,
        )

    def evaluate(
        self,
        signal: Signal,
        portfolio_state: PortfolioState,
    ) -> RiskDecision | RiskRejection:
        self._maybe_reset_daily_counters()

        # 1. Kill switch
        if self._kill_switch_active:
            return RiskRejection(
                signal=signal,
                reason=RejectionReason.KILL_SWITCH_ACTIVE,
                detail=self._kill_switch_reason,
            )

        # 2. Signal strength
        if signal.strength < self._config.min_signal_strength:
            return RiskRejection(
                signal=signal,
                reason=RejectionReason.LOW_SIGNAL_STRENGTH,
                detail=f"strength={signal.strength:.3f} < min={self._config.min_signal_strength}",
            )

        # 3. Max drawdown → triggers kill switch
        current_dd = float(portfolio_state.drawdown())
        if current_dd >= self._config.max_drawdown_pct:
            self.activate_kill_switch(
                f"Max drawdown reached: {current_dd:.2f}% >= {self._config.max_drawdown_pct}%"
            )
            return RiskRejection(
                signal=signal,
                reason=RejectionReason.EXCEEDS_MAX_DRAWDOWN,
                detail=f"drawdown={current_dd:.2f}%",
            )

        # 4. Daily loss limit — circuit breaker
        if self._daily_loss > Decimal("0"):
            equity = portfolio_state.total_equity()
            base = self._equity_at_day_start if self._equity_at_day_start > Decimal("0") else equity
            daily_loss_pct = float(self._daily_loss / base * 100) if base > 0 else 0.0
            if daily_loss_pct >= self._config.max_daily_loss_pct:
                self.activate_kill_switch(
                    f"Daily loss limit: {daily_loss_pct:.2f}% >= {self._config.max_daily_loss_pct}%"
                )
                return RiskRejection(
                    signal=signal,
                    reason=RejectionReason.DAILY_LOSS_LIMIT,
                    detail=f"daily_loss={daily_loss_pct:.2f}%",
                )

        # 4b. Consecutive losses — circuit breaker
        if self._consecutive_losses >= self._config.max_consecutive_losses:
            self.activate_kill_switch(
                f"Consecutive losses: {self._consecutive_losses} >= {self._config.max_consecutive_losses}"
            )
            return RiskRejection(
                signal=signal,
                reason=RejectionReason.CONSECUTIVE_LOSSES,
                detail=f"consecutive_losses={self._consecutive_losses}",
            )

        # 5. Daily trade limit
        if self._trades_today >= self._config.max_trades_per_day:
            return RiskRejection(
                signal=signal,
                reason=RejectionReason.EXCEEDS_POSITION_LIMIT,
                detail=f"trades_today={self._trades_today} >= max={self._config.max_trades_per_day}",
            )

        # 6. Exposure limit
        utilization = float(portfolio_state.utilization())
        if utilization >= self._config.max_total_exposure_pct:
            return RiskRejection(
                signal=signal,
                reason=RejectionReason.EXCEEDS_EXPOSURE_LIMIT,
                detail=f"utilization={utilization:.1f}% >= max={self._config.max_total_exposure_pct}%",
            )

        # 6b. Sector / asset cap check (cross-strategy)
        if (signal.direction in (SignalDirection.LONG, SignalDirection.SHORT)
                and self._sector_caps is not None):
            equity = portfolio_state.total_equity()
            notional = float(equity) * (self._config.max_position_size_pct / 100)
            allowed, cap_reason = self._sector_caps.check(signal.symbol.ticker, notional)
            if not allowed:
                return RiskRejection(
                    signal=signal,
                    reason=RejectionReason.SECTOR_CAP_EXCEEDED,
                    detail=cap_reason,
                )

        # 7. Existing position in same symbol
        existing_pos = portfolio_state.get_position(signal.symbol)
        if existing_pos and signal.direction != SignalDirection.FLAT:
            return RiskRejection(
                signal=signal,
                reason=RejectionReason.DUPLICATE_SIGNAL,
                detail=f"Already have position in {signal.symbol}",
            )

        # 7b. Correlated positions — too many in the same direction
        if signal.direction in (SignalDirection.LONG, SignalDirection.SHORT):
            target_side = OrderSide.BUY if signal.direction == SignalDirection.LONG else OrderSide.SELL
            same_direction = sum(
                1 for pos in portfolio_state.positions.values()
                if pos.side == target_side
            )
            if same_direction >= self._config.max_correlated_positions:
                return RiskRejection(
                    signal=signal,
                    reason=RejectionReason.EXCEEDS_POSITION_LIMIT,
                    detail=f"correlated_positions={same_direction} {target_side.value} >= max={self._config.max_correlated_positions}",
                )

        # 8. Capital sufficiency
        equity = portfolio_state.total_equity()
        min_notional = equity * Decimal(str(self._config.min_signal_strength / 100))
        if signal.price.value * Decimal("0.001") > equity:
            return RiskRejection(
                signal=signal,
                reason=RejectionReason.INSUFFICIENT_CAPITAL,
                detail=f"equity={equity:.2f} too low for price={signal.price}",
            )

        # 9. Compute size (scaled by edge monitor if edge is degrading)
        quantity = compute_position_size(signal, portfolio_state, self._config)
        edge_factor = self._edge_monitor.sizing_factor()
        if edge_factor < 1.0:
            quantity = Quantity(quantity.value * Decimal(str(edge_factor)))
        if quantity.value <= Decimal("0"):
            return RiskRejection(
                signal=signal,
                reason=RejectionReason.INSUFFICIENT_CAPITAL,
                detail="Computed position size is zero",
            )

        # 10. Risk score (informational)
        risk_score = min(current_dd / self._config.max_drawdown_pct, 1.0)

        # 11. Notify sector cap manager of position change
        if self._sector_caps is not None:
            notional = float(quantity.value * signal.price.value)
            if signal.direction in (SignalDirection.LONG, SignalDirection.SHORT):
                self._sector_caps.record_open(signal.symbol.ticker, notional)
            elif signal.direction == SignalDirection.FLAT:
                self._sector_caps.record_close(signal.symbol.ticker, notional)

        # Capture equity at start of day on first trade
        if self._equity_at_day_start == Decimal("0"):
            self._equity_at_day_start = portfolio_state.total_equity()

        self._trades_today += 1
        logger.info(
            "Risk APPROVED: %s | qty=%s | risk_score=%.2f",
            signal, quantity, risk_score,
        )

        return RiskDecision(
            signal=signal,
            approved_quantity=quantity,
            risk_score=risk_score,
            rationale=(
                f"drawdown={current_dd:.1f}% | "
                f"utilization={utilization:.1f}% | "
                f"strength={signal.strength:.2f}"
            ),
        )

    def activate_kill_switch(self, reason: str) -> None:
        self._kill_switch_active = True
        self._kill_switch_reason = reason
        logger.critical("KILL SWITCH ACTIVATED: %s", reason)

    def deactivate_kill_switch(self) -> None:
        self._kill_switch_active = False
        self._kill_switch_reason = ""
        logger.warning("Kill switch deactivated")

    @property
    def kill_switch_active(self) -> bool:
        return self._kill_switch_active

    def record_trade_result(self, pnl: Decimal) -> None:
        """
        Call after every closed trade with the net P&L.
        Updates daily loss accumulator, consecutive loss counter, and edge monitor.
        A winning trade resets the consecutive loss streak.
        """
        self._edge_monitor.record(pnl)
        if pnl < Decimal("0"):
            self._daily_loss += abs(pnl)
            self._consecutive_losses += 1
            logger.info(
                "Trade loss recorded: pnl=%.2f | daily_loss=%.2f | consecutive=%d | edge=%s",
                float(pnl), float(self._daily_loss), self._consecutive_losses,
                self._edge_monitor.summary(),
            )
        else:
            self._consecutive_losses = 0

    def _maybe_reset_daily_counters(self) -> None:
        today = datetime.now(timezone.utc).date()
        if today != self._last_reset_date:
            self._trades_today = 0
            self._daily_loss = Decimal("0")
            self._equity_at_day_start = Decimal("0")
            self._last_reset_date = today
            # Auto-deactivate kill switch if it was triggered by daily loss limit only.
            # Consecutive losses require explicit human deactivation.
            if (
                self._kill_switch_active
                and "Daily loss limit" in self._kill_switch_reason
            ):
                self.deactivate_kill_switch()
                logger.info("Kill switch auto-deactivated: new trading day started")
            logger.info("Daily counters reset for %s", today)
