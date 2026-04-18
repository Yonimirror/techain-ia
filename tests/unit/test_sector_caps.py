"""Tests for SectorCapManager and tier-based exposure enforcement."""
import pytest
from core.risk_engine.sector_caps import SectorCapManager, MAX_ASSET_PCT, MAX_SECTOR_PCT, MAX_TOTAL_PCT


class TestSectorCapManager:

    def test_allows_when_empty(self):
        mgr = SectorCapManager(total_capital=1_000_000)
        allowed, reason = mgr.check("NVDA", 50_000)
        assert allowed
        assert reason == ""

    def test_blocks_total_cap(self):
        mgr = SectorCapManager(total_capital=1_000_000)
        # Fill up to 55% (under 60% cap)
        mgr.record_open("BTC", 550_000)
        # Adding 60k would push to 61% → blocked
        allowed, reason = mgr.check("NVDA", 60_000)
        assert not allowed
        assert "total_exposure" in reason
        assert str(int(MAX_TOTAL_PCT)) in reason

    def test_blocks_asset_cap(self):
        mgr = SectorCapManager(total_capital=1_000_000)
        # NVDA already at 15%
        mgr.record_open("NVDA", 150_000)
        # Adding 60k would push NVDA to 21% → blocked
        allowed, reason = mgr.check("NVDA", 60_000)
        assert not allowed
        assert "NVDA_exposure" in reason

    def test_blocks_sector_cap(self):
        mgr = SectorCapManager(total_capital=1_000_000)
        # Semis: NVDA 15% + AVGO 15% = 30%
        mgr.record_open("NVDA", 150_000)
        mgr.record_open("AVGO", 150_000)
        # Adding SMH 60k = 36% semis → blocked
        allowed, reason = mgr.check("SMH", 60_000)
        assert not allowed
        assert "semis_sector" in reason

    def test_allows_different_sector(self):
        mgr = SectorCapManager(total_capital=1_000_000)
        # Semis at 30%
        mgr.record_open("NVDA", 150_000)
        mgr.record_open("AVGO", 150_000)
        # Energy is a different sector — not affected
        allowed, reason = mgr.check("XLE", 50_000)
        assert allowed

    def test_record_close_frees_capacity(self):
        mgr = SectorCapManager(total_capital=1_000_000)
        mgr.record_open("NVDA", 200_000)
        # NVDA at 20% — adding more blocked
        allowed, _ = mgr.check("NVDA", 10_000)
        assert not allowed
        # After closing, capacity freed
        mgr.record_close("NVDA", 200_000)
        allowed, reason = mgr.check("NVDA", 10_000)
        assert allowed

    def test_summary_structure(self):
        mgr = SectorCapManager(total_capital=1_000_000)
        mgr.record_open("NVDA", 50_000)
        mgr.record_open("XLE", 30_000)
        s = mgr.summary()
        assert "total_pct" in s
        assert abs(s["total_pct"] - 8.0) < 0.01
        assert "semis" in s["by_sector"]
        assert "energy" in s["by_sector"]

    def test_unknown_symbol_no_sector_cap(self):
        """Symbol not in SECTOR_MAP skips sector cap but respects asset/total caps."""
        mgr = SectorCapManager(total_capital=1_000_000)
        allowed, reason = mgr.check("UNKNOWN_TICKER", 50_000)
        assert allowed

    def test_zero_total_capital_always_allows(self):
        mgr = SectorCapManager(total_capital=0)
        allowed, _ = mgr.check("NVDA", 999_999_999)
        assert allowed


class TestSectorCapScenario:
    """Reproduce the selloff-of-semis scenario from tier_allocation_system.md"""

    def test_selloff_semis_capped_at_35pct(self):
        """
        NVDA×4 + AVGO×3 + SMH×2 signals all fire simultaneously.
        Without caps: 45% in semis.
        With caps: blocked at 35%.

        Scenario: shared portfolio of $1M, 5% position = $50k each.
        35% sector cap = $350k → 7 positions allowed, 8th and 9th blocked.
        """
        total = 1_000_000        # $1M shared portfolio
        position_size = 50_000   # 5% = $50k per position
        mgr = SectorCapManager(total_capital=total)

        approved = 0
        rejected = 0
        for symbol, count in [("NVDA", 4), ("AVGO", 3), ("SMH", 2)]:
            for _ in range(count):
                allowed, reason = mgr.check(symbol, position_size)
                if allowed:
                    mgr.record_open(symbol, position_size)
                    approved += 1
                else:
                    rejected += 1

        sector_exposure = mgr.summary()["by_sector"].get("semis", 0)
        assert sector_exposure <= MAX_SECTOR_PCT, (
            f"Semis exposure {sector_exposure:.1f}% exceeds {MAX_SECTOR_PCT}%"
        )
        assert rejected > 0, "Some positions should have been rejected"
        assert approved < 9, "Not all 9 positions should be open"
