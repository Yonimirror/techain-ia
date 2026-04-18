"""
Monte Carlo simulation for strategy robustness testing.

Generates N permutations of the trade sequence to estimate the
distribution of outcomes and assess strategy reliability.
"""
from __future__ import annotations
import random
from dataclasses import dataclass

from core.backtesting.metrics import compute_metrics, BacktestMetrics


@dataclass
class MonteCarloResult:
    n_simulations: int
    sharpe_mean: float
    sharpe_std: float
    sharpe_p5: float          # 5th percentile (worst 5%)
    max_drawdown_mean: float
    max_drawdown_p95: float   # 95th percentile (worst 5%)
    total_return_mean: float
    total_return_p5: float
    probability_of_loss: float  # % of simulations ending negative
    win_rate_mean: float


def run_monte_carlo(
    trade_pnl_pct: list[float],
    initial_equity: float = 100_000.0,
    n_simulations: int = 1000,
    seed: int | None = 42,
) -> MonteCarloResult:
    """
    Randomly shuffle trade sequence N times and compute metrics each time.

    This tests whether strategy performance is robust to trade ordering,
    or whether it was lucky with a specific sequence.

    Args:
        trade_pnl_pct: List of per-trade returns in %.
        initial_equity: Starting equity for equity curve reconstruction.
        n_simulations: Number of Monte Carlo runs.
        seed: Random seed for reproducibility.
    """
    if not trade_pnl_pct:
        return MonteCarloResult(
            n_simulations=0, sharpe_mean=0.0, sharpe_std=0.0, sharpe_p5=0.0,
            max_drawdown_mean=0.0, max_drawdown_p95=0.0, total_return_mean=0.0,
            total_return_p5=0.0, probability_of_loss=0.0, win_rate_mean=0.0,
        )

    rng = random.Random(seed)
    sharpes: list[float] = []
    max_dds: list[float] = []
    total_returns: list[float] = []
    win_rates: list[float] = []

    for _ in range(n_simulations):
        shuffled = trade_pnl_pct[:]
        rng.shuffle(shuffled)

        equity_curve = _build_equity_curve(shuffled, initial_equity)
        metrics = compute_metrics(equity_curve, shuffled)

        sharpes.append(metrics.sharpe_ratio)
        max_dds.append(metrics.max_drawdown_pct)
        total_returns.append(metrics.total_return_pct)
        win_rates.append(metrics.win_rate_pct)

    n = len(sharpes)
    sharpes_sorted = sorted(sharpes)
    returns_sorted = sorted(total_returns)
    dds_sorted = sorted(max_dds, reverse=True)

    p5_idx = max(0, int(n * 0.05) - 1)
    p95_idx = max(0, int(n * 0.95) - 1)

    return MonteCarloResult(
        n_simulations=n_simulations,
        sharpe_mean=sum(sharpes) / n,
        sharpe_std=_std(sharpes),
        sharpe_p5=sharpes_sorted[p5_idx],
        max_drawdown_mean=sum(max_dds) / n,
        max_drawdown_p95=dds_sorted[p95_idx],
        total_return_mean=sum(total_returns) / n,
        total_return_p5=returns_sorted[p5_idx],
        probability_of_loss=sum(1 for r in total_returns if r < 0) / n * 100,
        win_rate_mean=sum(win_rates) / n,
    )


def _build_equity_curve(pnl_pct_list: list[float], initial: float) -> list[float]:
    curve = [initial]
    for pct in pnl_pct_list:
        curve.append(curve[-1] * (1 + pct / 100))
    return curve


def _std(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    return variance ** 0.5
