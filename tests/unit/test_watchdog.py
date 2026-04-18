"""Tests for execution watchdog (Feature 6)."""
import pytest
from decimal import Decimal
from datetime import datetime

from core.execution_engine.watchdog import (
    ExecutionWatchdog, FillComparison, WatchdogReport,
)


def _make_comparison(
    real_price: float = 50010.0,
    sim_price: float = 50000.0,
    side: str = "BUY",
    qty: float = 0.1,
    real_fees: float = 5.0,
    sim_fees: float = 5.0,
) -> FillComparison:
    return FillComparison(
        order_id="test-001",
        symbol="BTC",
        side=side,
        quantity=Decimal(str(qty)),
        real_fill_price=Decimal(str(real_price)),
        simulated_fill_price=Decimal(str(sim_price)),
        real_fees=Decimal(str(real_fees)),
        simulated_fees=Decimal(str(sim_fees)),
    )


class TestFillComparison:
    def test_buy_slippage_positive_when_overpaid(self):
        comp = _make_comparison(real_price=50050, sim_price=50000, side="BUY")
        assert comp.price_slippage_bps > 0  # Paid more = bad

    def test_buy_slippage_negative_when_underpaid(self):
        comp = _make_comparison(real_price=49950, sim_price=50000, side="BUY")
        assert comp.price_slippage_bps < 0  # Paid less = good

    def test_sell_slippage_positive_when_received_less(self):
        comp = _make_comparison(real_price=49950, sim_price=50000, side="SELL")
        assert comp.price_slippage_bps > 0  # Received less = bad

    def test_sell_slippage_negative_when_received_more(self):
        comp = _make_comparison(real_price=50050, sim_price=50000, side="SELL")
        assert comp.price_slippage_bps < 0  # Received more = good

    def test_zero_slippage(self):
        comp = _make_comparison(real_price=50000, sim_price=50000)
        assert comp.price_slippage_bps == 0.0

    def test_total_cost_diff_buy(self):
        comp = _make_comparison(
            real_price=50010, sim_price=50000,
            side="BUY", qty=0.1,
            real_fees=6.0, sim_fees=5.0,
        )
        # Real cost: 50010*0.1 + 6 = 5007
        # Sim cost:  50000*0.1 + 5 = 5005
        # Diff: 2.0
        assert comp.total_cost_diff == Decimal("2.0")

    def test_total_cost_diff_sell(self):
        comp = _make_comparison(
            real_price=49990, sim_price=50000,
            side="SELL", qty=0.1,
            real_fees=6.0, sim_fees=5.0,
        )
        # Sim proceeds: 50000*0.1 - 5 = 4995
        # Real proceeds: 49990*0.1 - 6 = 4993
        # Diff (sim - real): 2.0
        assert comp.total_cost_diff == Decimal("2.0")

    def test_to_dict(self):
        comp = _make_comparison()
        d = comp.to_dict()
        assert "order_id" in d
        assert "price_slippage_bps" in d
        assert "timestamp" in d


class TestWatchdogReport:
    def test_empty_report(self):
        watchdog = ExecutionWatchdog(persist=False)
        report = watchdog.report()
        assert report.total_fills == 0
        assert not report.alert_triggered

    def test_no_alert_under_threshold(self):
        watchdog = ExecutionWatchdog(alert_threshold_bps=20.0, min_fills_for_alert=3, persist=False)
        # 5 bps slippage each
        for _ in range(5):
            watchdog.record(_make_comparison(real_price=50025, sim_price=50000))
        report = watchdog.report()
        assert report.total_fills == 5
        assert report.mean_slippage_bps == pytest.approx(5.0, abs=0.1)
        assert not report.alert_triggered

    def test_alert_when_mean_exceeds_threshold(self):
        watchdog = ExecutionWatchdog(alert_threshold_bps=10.0, min_fills_for_alert=3, persist=False)
        # ~20 bps slippage each
        for _ in range(5):
            watchdog.record(_make_comparison(real_price=50100, sim_price=50000))
        report = watchdog.report()
        assert report.alert_triggered
        assert "exceeds threshold" in report.alert_reason

    def test_no_alert_below_min_fills(self):
        watchdog = ExecutionWatchdog(alert_threshold_bps=5.0, min_fills_for_alert=10, persist=False)
        # High slippage but only 3 fills
        for _ in range(3):
            watchdog.record(_make_comparison(real_price=50200, sim_price=50000))
        report = watchdog.report()
        assert not report.alert_triggered  # Not enough data

    def test_last_n_report(self):
        watchdog = ExecutionWatchdog(persist=False)
        # First 5: low slippage
        for _ in range(5):
            watchdog.record(_make_comparison(real_price=50001, sim_price=50000))
        # Last 5: high slippage
        for _ in range(5):
            watchdog.record(_make_comparison(real_price=50100, sim_price=50000))
        report_all = watchdog.report()
        report_last5 = watchdog.report(last_n=5)
        # Last 5 should have higher mean slippage
        assert report_last5.mean_slippage_bps > report_all.mean_slippage_bps

    def test_record_fill_convenience(self):
        watchdog = ExecutionWatchdog(persist=False)
        comp = watchdog.record_fill(
            order_id="o-1",
            symbol="ETH",
            side="BUY",
            quantity=Decimal("1"),
            real_price=Decimal("3010"),
            simulated_price=Decimal("3000"),
            real_fees=Decimal("3"),
            simulated_fees=Decimal("3"),
        )
        assert comp.price_slippage_bps > 0
        assert watchdog.report().total_fills == 1


class TestP95Alert:
    def test_p95_extreme_alert(self):
        watchdog = ExecutionWatchdog(alert_threshold_bps=10.0, min_fills_for_alert=5, persist=False)
        # 19 normal fills
        for _ in range(19):
            watchdog.record(_make_comparison(real_price=50005, sim_price=50000))
        # 1 extreme fill
        watchdog.record(_make_comparison(real_price=50500, sim_price=50000))
        report = watchdog.report()
        assert report.p95_slippage_bps > 30  # threshold * 3
        # Mean might be low but p95 triggers alert
        if report.mean_slippage_bps <= 10.0:
            assert report.alert_triggered
            assert "P95" in report.alert_reason or "extreme" in report.alert_reason.lower()
