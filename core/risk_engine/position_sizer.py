"""
Position sizing module.

Implements:
- Fixed fractional sizing
- Kelly Criterion (capped)
- Volatility-adjusted sizing (ATR-based)
"""
from __future__ import annotations
from decimal import Decimal

from core.domain.entities import PortfolioState
from core.domain.entities.signal import Signal
from core.domain.value_objects import Quantity, Price
from core.risk_engine.config import RiskConfig


def fixed_fractional_size(
    portfolio: PortfolioState,
    risk_pct: float,
    entry_price: Price,
    stop_price: Price | None = None,
) -> Quantity:
    """
    Size = (equity * risk_pct) / entry_price
    If stop_price is provided, uses risk-per-trade sizing instead.
    """
    equity = portfolio.total_equity()
    risk_amount = equity * Decimal(str(risk_pct / 100))

    if stop_price is not None:
        risk_per_unit = abs(entry_price.value - stop_price.value)
        if risk_per_unit == 0:
            return Quantity.of(0)
        return Quantity(risk_amount / risk_per_unit)

    if entry_price.value == 0:
        return Quantity.of(0)
    return Quantity(risk_amount / entry_price.value)


def kelly_size(
    portfolio: PortfolioState,
    win_rate: float,
    avg_win: float,
    avg_loss: float,
    entry_price: Price,
    kelly_fraction: float = 0.25,
    max_pct: float = 10.0,
) -> Quantity:
    """
    Kelly Criterion (fractional).

    f* = (p * b - q) / b
    where:
        p = win rate
        q = 1 - p
        b = avg_win / avg_loss

    The result is multiplied by kelly_fraction (fractional Kelly)
    and capped at max_pct of equity.
    """
    if avg_loss == 0 or avg_win == 0:
        return Quantity.of(0)

    b = avg_win / avg_loss
    q = 1.0 - win_rate
    full_kelly = (win_rate * b - q) / b

    if full_kelly <= 0:
        return Quantity.of(0)

    fraction = min(full_kelly * kelly_fraction, max_pct / 100.0)
    equity = portfolio.total_equity()
    capital_to_deploy = equity * Decimal(str(fraction))

    if entry_price.value == 0:
        return Quantity.of(0)

    return Quantity(capital_to_deploy / entry_price.value)


def compute_position_size(
    signal: Signal,
    portfolio: PortfolioState,
    config: RiskConfig,
) -> Quantity:
    """
    Main sizing function used by the risk engine.

    Uses fixed fractional sizing scaled by signal strength,
    capped by the risk config limits.
    """
    equity = portfolio.total_equity()
    if equity == 0:
        return Quantity.of(0)

    # Base size: max_position_size_pct scaled by signal strength
    effective_pct = config.max_position_size_pct * signal.strength
    effective_pct = min(effective_pct, config.max_position_size_pct)

    # Soft drawdown reduction: scale back at soft_drawdown_pct
    current_drawdown = float(portfolio.drawdown())
    if current_drawdown >= config.soft_drawdown_pct:
        reduction_factor = 1.0 - (
            (current_drawdown - config.soft_drawdown_pct)
            / (config.max_drawdown_pct - config.soft_drawdown_pct + 0.001)
        )
        effective_pct *= max(reduction_factor, 0.1)  # min 10% of original

    capital_to_deploy = equity * Decimal(str(effective_pct / 100))

    if signal.price.value == 0:
        return Quantity.of(0)

    return Quantity(capital_to_deploy / signal.price.value)
