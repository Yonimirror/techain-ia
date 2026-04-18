"""
SectorCapManager — cross-strategy exposure enforcement.

Each strategy runs with an isolated PortfolioEngine, so sector/asset caps
must be tracked here as a shared singleton across all running strategies.

Caps (from docs/tier_allocation_system.md):
  - Max 20% of total capital in one asset
  - Max 35% of total capital in one sector
  - Max 60% of total capital invested simultaneously
"""
from __future__ import annotations
import logging

logger = logging.getLogger(__name__)

# Sector membership — used to enforce the 35% sector cap
SECTOR_MAP: dict[str, str] = {
    "NVDA": "semis",  "AVGO": "semis",  "SMH": "semis",
    "FCX":  "metals", "GLD":  "metals",
    "CL=F": "energy", "XLE":  "energy",
    "BTC":  "crypto", "ETH":  "crypto", "SOL": "crypto",
    "MSFT": "macro",  "SPY":  "macro",
}

MAX_ASSET_PCT  = 20.0   # max % of total capital in one asset
MAX_SECTOR_PCT = 35.0   # max % of total capital in one sector
MAX_TOTAL_PCT  = 60.0   # max % of total capital deployed at once


class SectorCapManager:
    """
    Tracks notional exposure across all strategy instances.

    Usage:
        manager = SectorCapManager(total_capital=30_000_000)

        allowed, reason = manager.check("NVDA", notional=100_000)
        if allowed:
            manager.record_open("NVDA", 100_000)

        manager.record_close("NVDA", 100_000)
    """

    def __init__(self, total_capital: float) -> None:
        self._total = total_capital
        # symbol → current open notional (USD)
        self._open: dict[str, float] = {}

    def check(self, symbol: str, notional: float) -> tuple[bool, str]:
        """
        Return (True, "") if adding `notional` to `symbol` is within all caps.
        Return (False, reason) otherwise.
        """
        if self._total <= 0:
            return True, ""

        current_total = sum(self._open.values())
        new_total_pct = (current_total + notional) / self._total * 100
        if new_total_pct > MAX_TOTAL_PCT:
            return False, (
                f"total_exposure={new_total_pct:.1f}% > cap={MAX_TOTAL_PCT}% "
                f"(current={current_total/self._total*100:.1f}%)"
            )

        current_asset = self._open.get(symbol, 0.0)
        new_asset_pct = (current_asset + notional) / self._total * 100
        if new_asset_pct > MAX_ASSET_PCT:
            return False, (
                f"{symbol}_exposure={new_asset_pct:.1f}% > cap={MAX_ASSET_PCT}% "
                f"(current={current_asset/self._total*100:.1f}%)"
            )

        sector = SECTOR_MAP.get(symbol)
        if sector:
            sector_peers = [s for s, sec in SECTOR_MAP.items() if sec == sector]
            current_sector = sum(self._open.get(s, 0.0) for s in sector_peers)
            new_sector_pct = (current_sector + notional) / self._total * 100
            if new_sector_pct > MAX_SECTOR_PCT:
                return False, (
                    f"{sector}_sector={new_sector_pct:.1f}% > cap={MAX_SECTOR_PCT}% "
                    f"(current={current_sector/self._total*100:.1f}%)"
                )

        return True, ""

    def record_open(self, symbol: str, notional: float) -> None:
        self._open[symbol] = self._open.get(symbol, 0.0) + notional
        logger.debug(
            "SectorCap.open %s +%.0f | total_open=%.0f (%.1f%%)",
            symbol, notional, sum(self._open.values()),
            sum(self._open.values()) / self._total * 100 if self._total else 0,
        )

    def record_close(self, symbol: str, notional: float) -> None:
        self._open[symbol] = max(0.0, self._open.get(symbol, 0.0) - notional)
        logger.debug("SectorCap.close %s -%.0f", symbol, notional)

    def summary(self) -> dict:
        if not self._total:
            return {}
        by_sector: dict[str, float] = {}
        for sym, notional in self._open.items():
            sector = SECTOR_MAP.get(sym, "other")
            by_sector[sector] = by_sector.get(sector, 0.0) + notional
        return {
            "total_pct": sum(self._open.values()) / self._total * 100,
            "by_asset":  {s: n / self._total * 100 for s, n in self._open.items() if n > 0},
            "by_sector": {s: n / self._total * 100 for s, n in by_sector.items() if n > 0},
        }
