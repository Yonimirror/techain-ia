"""
Paper Trading State Persistence.

Saves and loads portfolio + risk engine state between paper trading runs
so that the paper trader maintains continuous positions and equity tracking.

State is stored as human-readable JSON in data/paper_state/{session_id}.json.
"""
from __future__ import annotations
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from core.domain.entities import Trade, Position, PortfolioState
from core.domain.entities.order import OrderSide
from core.domain.entities.trade import TradeStatus
from core.domain.value_objects import Symbol, Price, Quantity

logger = logging.getLogger(__name__)

STATE_DIR = Path("data/paper_state")


def _serialize_symbol(symbol: Symbol) -> dict:
    return {"ticker": symbol.ticker, "exchange": symbol.exchange}


def _deserialize_symbol(d: dict) -> Symbol:
    return Symbol.of(d["ticker"], d["exchange"])


def _serialize_position(pos: Position) -> dict:
    return {
        "symbol": _serialize_symbol(pos.symbol),
        "side": pos.side.value,
        "quantity": str(pos.quantity.value),
        "average_entry_price": str(pos.average_entry_price.value),
        "opened_at": pos.opened_at.isoformat(),
        "trade_ids": [str(uid) for uid in pos.trade_ids],
        "last_updated": pos.last_updated.isoformat(),
    }


def _deserialize_position(d: dict) -> Position:
    return Position(
        symbol=_deserialize_symbol(d["symbol"]),
        side=OrderSide(d["side"]),
        quantity=Quantity(Decimal(d["quantity"])),
        average_entry_price=Price(Decimal(d["average_entry_price"])),
        opened_at=datetime.fromisoformat(d["opened_at"]),
        trade_ids=[UUID(uid) for uid in d["trade_ids"]],
        last_updated=datetime.fromisoformat(d["last_updated"]),
    )


def _serialize_trade(trade: Trade) -> dict:
    return {
        "id": str(trade.id),
        "symbol": _serialize_symbol(trade.symbol),
        "side": trade.side.value,
        "entry_price": str(trade.entry_price.value),
        "quantity": str(trade.quantity.value),
        "entry_order_id": str(trade.entry_order_id),
        "strategy_id": trade.strategy_id,
        "status": trade.status.value,
        "opened_at": trade.opened_at.isoformat(),
        "exit_price": str(trade.exit_price.value) if trade.exit_price else None,
        "exit_order_id": str(trade.exit_order_id) if trade.exit_order_id else None,
        "closed_at": trade.closed_at.isoformat() if trade.closed_at else None,
        "fees": str(trade.fees),
        "metadata": trade.metadata,
    }


def _deserialize_trade(d: dict) -> Trade:
    return Trade(
        id=UUID(d["id"]),
        symbol=_deserialize_symbol(d["symbol"]),
        side=OrderSide(d["side"]),
        entry_price=Price(Decimal(d["entry_price"])),
        quantity=Quantity(Decimal(d["quantity"])),
        entry_order_id=UUID(d["entry_order_id"]),
        strategy_id=d["strategy_id"],
        status=TradeStatus(d["status"]),
        opened_at=datetime.fromisoformat(d["opened_at"]),
        exit_price=Price(Decimal(d["exit_price"])) if d["exit_price"] else None,
        exit_order_id=UUID(d["exit_order_id"]) if d["exit_order_id"] else None,
        closed_at=datetime.fromisoformat(d["closed_at"]) if d["closed_at"] else None,
        fees=Decimal(d["fees"]),
        metadata=d.get("metadata", {}),
    )


@dataclass
class PaperTradingState:
    session_id: str
    cash: Decimal
    initial_capital: Decimal
    peak_equity: Decimal
    positions: list[dict] = field(default_factory=list)
    open_trades: list[dict] = field(default_factory=list)
    closed_trades: list[dict] = field(default_factory=list)
    equity_curve: list[dict] = field(default_factory=list)
    last_bar_timestamp: str = ""
    saved_at: str = ""
    risk_state: dict = field(default_factory=dict)


def save_state(
    session_id: str,
    portfolio: "PortfolioEngine",  # noqa: F821
    risk_engine: "RiskEngine",  # noqa: F821
    last_bar_timestamp: datetime | str,
) -> Path:
    """Serialize portfolio + risk state to JSON."""
    from core.portfolio_engine.engine import PortfolioEngine
    from core.risk_engine.engine import RiskEngine

    STATE_DIR.mkdir(parents=True, exist_ok=True)

    # Normalize last_bar_timestamp to string
    if isinstance(last_bar_timestamp, str):
        ts_str = last_bar_timestamp
    else:
        ts_str = last_bar_timestamp.isoformat()

    state = {
        "session_id": session_id,
        "cash": str(portfolio.state.cash),
        "initial_capital": str(portfolio.state.initial_capital),
        "peak_equity": str(portfolio.state.peak_equity),
        "positions": [
            _serialize_position(pos) for pos in portfolio.state.positions.values()
        ],
        "open_trades": [
            _serialize_trade(trade) for trade in portfolio.open_trades.values()
        ],
        "closed_trades": [
            _serialize_trade(trade) for trade in portfolio.closed_trades
        ],
        "equity_curve": [
            {"timestamp": ts.isoformat(), "equity": str(eq)}
            for ts, eq in portfolio.get_equity_curve()
        ],
        "last_bar_timestamp": ts_str,
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "risk_state": {
            "kill_switch_active": risk_engine._kill_switch_active,
            "kill_switch_reason": risk_engine._kill_switch_reason,
            "consecutive_losses": risk_engine._consecutive_losses,
            "daily_loss": str(risk_engine._daily_loss),
            "equity_at_day_start": str(risk_engine._equity_at_day_start),
            "last_reset_date": risk_engine._last_reset_date.isoformat(),
            "trades_today": risk_engine._trades_today,
            "edge_monitor": {
                "total_trades": risk_engine._edge_monitor._total_trades,
                "ewma_win": risk_engine._edge_monitor._ewma_win,
                "ewma_pf": risk_engine._edge_monitor._ewma_pf,
                "gross_win": risk_engine._edge_monitor._gross_win,
                "gross_loss": risk_engine._edge_monitor._gross_loss,
            },
        },
    }

    path = STATE_DIR / f"{session_id}.json"
    with open(path, "w") as f:
        json.dump(state, f, indent=2)

    logger.info("Paper state saved: %s (%d positions, %d closed trades)",
                path, len(state["positions"]), len(state["closed_trades"]))
    return path


def load_state(session_id: str) -> PaperTradingState | None:
    """Load paper trading state from JSON. Returns None if no state file exists."""
    path = STATE_DIR / f"{session_id}.json"
    if not path.exists():
        return None

    with open(path) as f:
        data = json.load(f)

    logger.info("Paper state loaded: %s (last_bar=%s)", path, data["last_bar_timestamp"])

    return PaperTradingState(
        session_id=data["session_id"],
        cash=Decimal(data["cash"]),
        initial_capital=Decimal(data["initial_capital"]),
        peak_equity=Decimal(data["peak_equity"]),
        positions=data["positions"],
        open_trades=data["open_trades"],
        closed_trades=data["closed_trades"],
        equity_curve=data["equity_curve"],
        last_bar_timestamp=data["last_bar_timestamp"],
        saved_at=data["saved_at"],
        risk_state=data["risk_state"],
    )


def delete_state(session_id: str) -> bool:
    """Delete state file for a session. Returns True if deleted."""
    path = STATE_DIR / f"{session_id}.json"
    if path.exists():
        path.unlink()
        logger.info("Paper state deleted: %s", path)
        return True
    return False


def rebuild_portfolio(state: PaperTradingState) -> "PortfolioEngine":  # noqa: F821
    """Reconstruct a PortfolioEngine from saved state."""
    from core.portfolio_engine.engine import PortfolioEngine

    portfolio = PortfolioEngine(state.initial_capital)

    # Restore cash and peak equity
    portfolio._state.cash = state.cash
    portfolio._state.peak_equity = state.peak_equity

    # Restore positions
    for pos_dict in state.positions:
        pos = _deserialize_position(pos_dict)
        portfolio._state.set_position(pos)

    # Restore open trades
    for trade_dict in state.open_trades:
        trade = _deserialize_trade(trade_dict)
        portfolio._open_trades[str(trade.symbol)] = trade

    # Restore closed trades
    for trade_dict in state.closed_trades:
        trade = _deserialize_trade(trade_dict)
        portfolio._closed_trades.append(trade)

    # Restore equity curve
    for point in state.equity_curve:
        ts = datetime.fromisoformat(point["timestamp"])
        eq = Decimal(point["equity"])
        portfolio._equity_curve.append((ts, eq))

    logger.info(
        "Portfolio rebuilt: cash=%s | positions=%d | open_trades=%d | closed_trades=%d",
        state.cash, len(state.positions), len(state.open_trades), len(state.closed_trades),
    )
    return portfolio


def restore_risk_state(risk_engine: "RiskEngine", risk_state: dict) -> None:  # noqa: F821
    """Restore risk engine internal state from saved dict."""
    from datetime import date

    risk_engine._kill_switch_active = risk_state.get("kill_switch_active", False)
    risk_engine._kill_switch_reason = risk_state.get("kill_switch_reason", "")
    risk_engine._consecutive_losses = risk_state.get("consecutive_losses", 0)
    risk_engine._daily_loss = Decimal(risk_state.get("daily_loss", "0"))
    risk_engine._equity_at_day_start = Decimal(risk_state.get("equity_at_day_start", "0"))
    risk_engine._trades_today = risk_state.get("trades_today", 0)

    last_reset = risk_state.get("last_reset_date")
    if last_reset:
        risk_engine._last_reset_date = date.fromisoformat(last_reset)

    # Restore edge monitor state
    em = risk_state.get("edge_monitor", {})
    if em:
        risk_engine._edge_monitor._total_trades = em.get("total_trades", 0)
        risk_engine._edge_monitor._ewma_win = em.get("ewma_win")
        risk_engine._edge_monitor._ewma_pf = em.get("ewma_pf")
        risk_engine._edge_monitor._gross_win = em.get("gross_win", 0.0)
        risk_engine._edge_monitor._gross_loss = em.get("gross_loss", 0.0)

    logger.info(
        "Risk state restored: kill_switch=%s | consecutive_losses=%d | trades_today=%d",
        risk_engine._kill_switch_active, risk_engine._consecutive_losses,
        risk_engine._trades_today,
    )
