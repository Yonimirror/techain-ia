"""
Backtesting performance metrics.

All functions are pure: input list of returns/trades → output metric.
"""
from __future__ import annotations
import math
from dataclasses import dataclass
from decimal import Decimal


@dataclass
class BacktestMetrics:
    total_return_pct: float
    sharpe_ratio: float
    sortino_ratio: float
    max_drawdown_pct: float
    calmar_ratio: float
    win_rate_pct: float
    profit_factor: float
    total_trades: int
    avg_trade_duration_s: float
    avg_win_pct: float
    avg_loss_pct: float
    expectancy: float          # avg PnL per trade in %

    def __str__(self) -> str:
        return (
            f"Return:       {self.total_return_pct:+.2f}%\n"
            f"Sharpe:       {self.sharpe_ratio:.3f}\n"
            f"Sortino:      {self.sortino_ratio:.3f}\n"
            f"Max Drawdown: {self.max_drawdown_pct:.2f}%\n"
            f"Calmar:       {self.calmar_ratio:.3f}\n"
            f"Win Rate:     {self.win_rate_pct:.1f}%\n"
            f"Profit Factor:{self.profit_factor:.3f}\n"
            f"Trades:       {self.total_trades}\n"
            f"Expectancy:   {self.expectancy:+.3f}%"
        )


def compute_metrics(
    equity_curve: list[float],
    trades_pnl_pct: list[float],
    risk_free_rate: float = 0.05,
    periods_per_year: int = 252,
) -> BacktestMetrics:
    """
    Compute full performance metrics from equity curve and trade PnL list.

    Args:
        equity_curve: List of equity values over time.
        trades_pnl_pct: List of per-trade PnL in %.
        risk_free_rate: Annual risk-free rate (default 5%).
        periods_per_year: Number of periods per year (252 for daily).
    """
    if len(equity_curve) < 2:
        return _empty_metrics()

    returns = [
        (equity_curve[i] - equity_curve[i - 1]) / equity_curve[i - 1]
        for i in range(1, len(equity_curve))
    ]

    total_return = (equity_curve[-1] - equity_curve[0]) / equity_curve[0] * 100
    max_dd = max_drawdown(equity_curve)
    sharpe = sharpe_ratio(returns, risk_free_rate, periods_per_year)
    sortino = sortino_ratio(returns, risk_free_rate, periods_per_year)
    calmar = total_return / abs(max_dd) if max_dd != 0 else float("inf")

    wins = [p for p in trades_pnl_pct if p > 0]
    losses = [p for p in trades_pnl_pct if p <= 0]
    win_rate = len(wins) / len(trades_pnl_pct) * 100 if trades_pnl_pct else 0.0
    profit_factor = (
        abs(sum(wins) / sum(losses))
        if losses and sum(losses) != 0
        else float("inf")
    )
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    expectancy = sum(trades_pnl_pct) / len(trades_pnl_pct) if trades_pnl_pct else 0.0

    return BacktestMetrics(
        total_return_pct=total_return,
        sharpe_ratio=sharpe,
        sortino_ratio=sortino,
        max_drawdown_pct=max_dd,
        calmar_ratio=calmar,
        win_rate_pct=win_rate,
        profit_factor=profit_factor,
        total_trades=len(trades_pnl_pct),
        avg_trade_duration_s=0.0,   # populated by backtest runner
        avg_win_pct=avg_win,
        avg_loss_pct=avg_loss,
        expectancy=expectancy,
    )


def max_drawdown(equity_curve: list[float]) -> float:
    """Maximum peak-to-trough drawdown in %."""
    if not equity_curve:
        return 0.0
    peak = equity_curve[0]
    max_dd = 0.0
    for value in equity_curve:
        if value > peak:
            peak = value
        dd = (peak - value) / peak * 100
        if dd > max_dd:
            max_dd = dd
    return max_dd


def sharpe_ratio(
    returns: list[float],
    risk_free_rate: float = 0.05,
    periods_per_year: int = 252,
) -> float:
    """Annualized Sharpe ratio."""
    if len(returns) < 2:
        return 0.0
    n = len(returns)
    mean_r = sum(returns) / n
    rf_per_period = risk_free_rate / periods_per_year
    excess = [r - rf_per_period for r in returns]
    mean_excess = sum(excess) / n
    variance = sum((r - mean_excess) ** 2 for r in excess) / (n - 1)
    if variance == 0:
        # Zero variance: all returns identical.
        # Decision based on raw returns mean (not excess) to avoid
        # risk-free rate contaminating the zero-return case.
        return 0.0 if abs(mean_r) < 1e-10 else math.copysign(10.0, mean_excess)
    std = math.sqrt(variance)
    return (mean_excess / std) * math.sqrt(periods_per_year)


def sortino_ratio(
    returns: list[float],
    risk_free_rate: float = 0.05,
    periods_per_year: int = 252,
) -> float:
    """Annualized Sortino ratio (downside deviation only)."""
    if len(returns) < 2:
        return 0.0
    n = len(returns)
    rf_per_period = risk_free_rate / periods_per_year
    excess = [r - rf_per_period for r in returns]
    mean_excess = sum(excess) / n
    downside = [min(r, 0.0) ** 2 for r in excess]
    downside_var = sum(downside) / n
    downside_std = math.sqrt(downside_var) if downside_var > 0 else 1e-10
    return (mean_excess / downside_std) * math.sqrt(periods_per_year)


def _empty_metrics() -> BacktestMetrics:
    return BacktestMetrics(
        total_return_pct=0.0, sharpe_ratio=0.0, sortino_ratio=0.0,
        max_drawdown_pct=0.0, calmar_ratio=0.0, win_rate_pct=0.0,
        profit_factor=0.0, total_trades=0, avg_trade_duration_s=0.0,
        avg_win_pct=0.0, avg_loss_pct=0.0, expectancy=0.0,
    )
