from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

from core.domain.entities.position import Position
from core.domain.value_objects import Symbol, Price


@dataclass
class PortfolioState:
    """Snapshot of the portfolio at a point in time."""
    cash: Decimal
    initial_capital: Decimal
    positions: dict[str, Position] = field(default_factory=dict)  # symbol str -> Position
    peak_equity: Decimal = field(init=False)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        self.peak_equity = self.cash

    def get_position(self, symbol: Symbol) -> Position | None:
        return self.positions.get(str(symbol))

    def set_position(self, position: Position) -> None:
        if position.is_flat:
            self.positions.pop(str(position.symbol), None)
        else:
            self.positions[str(position.symbol)] = position

    def total_equity(self, current_prices: dict[str, Price] | None = None) -> Decimal:
        equity = self.cash
        if current_prices:
            for sym_str, pos in self.positions.items():
                price = current_prices.get(sym_str)
                if price:
                    equity += pos.notional_value + pos.unrealized_pnl(price)
        else:
            for pos in self.positions.values():
                equity += pos.notional_value
        return equity

    def drawdown(self, current_prices: dict[str, Price] | None = None) -> Decimal:
        equity = self.total_equity(current_prices)
        if equity > self.peak_equity:
            self.peak_equity = equity
        if self.peak_equity == 0:
            return Decimal("0")
        return (self.peak_equity - equity) / self.peak_equity * 100

    def utilization(self) -> Decimal:
        """Percentage of capital deployed."""
        invested = sum(p.notional_value for p in self.positions.values())
        total = self.total_equity()
        if total == 0:
            return Decimal("0")
        return invested / total * 100

    def update_timestamp(self) -> None:
        self.timestamp = datetime.now(timezone.utc)

    def __repr__(self) -> str:
        return (
            f"PortfolioState(cash={self.cash:.2f} | "
            f"positions={len(self.positions)} | "
            f"equity~={self.total_equity():.2f})"
        )
