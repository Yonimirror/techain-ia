"""Unit tests for backtesting metrics."""
import pytest
import math

from core.backtesting.metrics import (
    compute_metrics, max_drawdown, sharpe_ratio, sortino_ratio
)
from core.backtesting.monte_carlo import run_monte_carlo


class TestMaxDrawdown:
    def test_no_drawdown(self):
        equity = [100, 110, 120, 130]
        assert max_drawdown(equity) == 0.0

    def test_simple_drawdown(self):
        equity = [100, 80]  # 20% drawdown
        assert max_drawdown(equity) == pytest.approx(20.0)

    def test_partial_recovery(self):
        equity = [100, 50, 75]  # max DD = 50%
        assert max_drawdown(equity) == pytest.approx(50.0)


class TestSharpeRatio:
    def test_positive_sharpe(self):
        # Consistently positive returns → positive Sharpe
        returns = [0.01] * 252
        sr = sharpe_ratio(returns, risk_free_rate=0.0)
        assert sr > 0

    def test_zero_returns(self):
        returns = [0.0] * 100
        sr = sharpe_ratio(returns)
        assert sr == pytest.approx(0.0, abs=0.01)

    def test_negative_sharpe(self):
        returns = [-0.01] * 100
        sr = sharpe_ratio(returns, risk_free_rate=0.0)
        assert sr < 0


class TestComputeMetrics:
    def test_profitable_strategy(self):
        equity = [100_000 + i * 500 for i in range(100)]
        trades = [0.5] * 60 + [-0.3] * 40
        metrics = compute_metrics(equity, trades)
        assert metrics.total_return_pct > 0
        assert metrics.win_rate_pct == pytest.approx(60.0)
        assert metrics.total_trades == 100

    def test_empty_data(self):
        metrics = compute_metrics([], [])
        assert metrics.total_trades == 0
        assert metrics.sharpe_ratio == 0.0

    def test_profit_factor(self):
        equity = [100, 105, 110, 108, 112]
        trades = [5.0, 5.0, -2.0, 4.0]  # wins=14, losses=2 → PF=7
        metrics = compute_metrics(equity, trades)
        assert metrics.profit_factor == pytest.approx(7.0)


class TestMonteCarlo:
    def test_basic_run(self):
        trade_pnls = [1.0, -0.5, 2.0, -0.3, 1.5, 0.8, -0.2] * 20
        result = run_monte_carlo(trade_pnls, n_simulations=100, seed=42)
        assert result.n_simulations == 100
        assert 0 <= result.probability_of_loss <= 100

    def test_empty_trades(self):
        result = run_monte_carlo([])
        assert result.n_simulations == 0

    def test_reproducible(self):
        trades = [1.0, -0.5, 2.0] * 10
        r1 = run_monte_carlo(trades, n_simulations=50, seed=99)
        r2 = run_monte_carlo(trades, n_simulations=50, seed=99)
        assert r1.sharpe_mean == pytest.approx(r2.sharpe_mean)
