"""Tests for strategy rebalancer (Feature 7)."""
import pytest
from decimal import Decimal

from core.risk_engine.rebalancer import (
    StrategyRebalancer, StrategyPerformance, AllocationResult,
)


def _trades(wins: int, losses: int, win_pnl: float = 50, loss_pnl: float = -30) -> list[dict]:
    """Generate a list of trade dicts."""
    trades = []
    for _ in range(wins):
        trades.append({"pnl": Decimal(str(win_pnl))})
    for _ in range(losses):
        trades.append({"pnl": Decimal(str(loss_pnl))})
    return trades


class TestRebalancerWeights:
    def test_equal_weights_with_no_data(self):
        rb = StrategyRebalancer(min_trades_for_rebalance=5, persist=False)
        result = rb.compute_weights()
        assert result.total_strategies == 0

    def test_equal_weights_below_min_trades(self):
        rb = StrategyRebalancer(min_trades_for_rebalance=10, persist=False)
        rb.update_performance("strat_a", "BTC", "4h", _trades(2, 1))
        result = rb.compute_weights()
        assert result.weights.get("strat_a") == 1.0  # Not enough data

    def test_winner_gets_more_weight(self):
        rb = StrategyRebalancer(min_trades_for_rebalance=5, persist=False)
        # Strategy A: great performance
        rb.update_performance("strat_a", "BTC", "4h", _trades(8, 2))
        # Strategy B: poor performance
        rb.update_performance("strat_b", "ETH", "4h", _trades(2, 8))
        result = rb.compute_weights()
        assert result.weights["strat_a"] > result.weights["strat_b"]

    def test_weight_bounded_by_min_max(self):
        rb = StrategyRebalancer(min_weight=0.3, max_weight=2.0, min_trades_for_rebalance=3, persist=False)
        rb.update_performance("winner", "BTC", "4h", _trades(10, 0, win_pnl=100))
        rb.update_performance("loser", "ETH", "4h", _trades(0, 10))
        result = rb.compute_weights()
        assert result.weights["winner"] <= 2.0
        assert result.weights["loser"] >= 0.3

    def test_single_strategy_weight_within_bounds(self):
        rb = StrategyRebalancer(min_weight=0.3, max_weight=2.0, min_trades_for_rebalance=3, persist=False)
        rb.update_performance("only_one", "BTC", "4h", _trades(5, 2))
        result = rb.compute_weights()
        # Single strategy: score/avg = 1.0 but score method adds bonuses
        # Just verify it's within bounds
        assert 0.3 <= result.weights["only_one"] <= 2.0

    def test_get_weight_default(self):
        rb = StrategyRebalancer(persist=False)
        assert rb.get_weight("unknown") == 1.0

    def test_get_weight_after_compute(self):
        rb = StrategyRebalancer(min_trades_for_rebalance=3, persist=False)
        rb.update_performance("strat_a", "BTC", "4h", _trades(8, 2))
        rb.update_performance("strat_b", "ETH", "4h", _trades(3, 7))
        rb.compute_weights()
        assert rb.get_weight("strat_a") > rb.get_weight("strat_b")


class TestStrategyPerformance:
    def test_win_rate(self):
        perf = StrategyPerformance(
            strategy_id="test",
            symbol="BTC",
            timeframe="4h",
            recent_trades=10,
            recent_wins=7,
        )
        assert perf.win_rate == 0.7

    def test_profit_factor(self):
        perf = StrategyPerformance(
            strategy_id="test",
            symbol="BTC",
            timeframe="4h",
            recent_trades=10,
            recent_gross_profit=Decimal("500"),
            recent_gross_loss=Decimal("-200"),
        )
        assert perf.profit_factor == pytest.approx(2.5)

    def test_profit_factor_no_losses(self):
        perf = StrategyPerformance(
            strategy_id="test",
            symbol="BTC",
            timeframe="4h",
            recent_trades=5,
            recent_gross_profit=Decimal("500"),
            recent_gross_loss=Decimal("0"),
        )
        assert perf.profit_factor == float("inf")

    def test_score_neutral_with_few_trades(self):
        perf = StrategyPerformance(
            strategy_id="test",
            symbol="BTC",
            timeframe="4h",
            recent_trades=2,
        )
        assert perf.score == 1.0  # Not enough data → neutral


class TestRebalancerSummary:
    def test_summary_structure(self):
        rb = StrategyRebalancer(min_trades_for_rebalance=3, persist=False)
        rb.update_performance("strat_a", "BTC", "4h", _trades(5, 2))
        rb.compute_weights()
        s = rb.summary()
        assert "strategies" in s
        assert "strat_a" in s["strategies"]
        assert "weight" in s["strategies"]["strat_a"]
        assert "win_rate" in s["strategies"]["strat_a"]
