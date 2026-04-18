"""
Backtesting runner.

Replays historical MarketData bar by bar, feeding it through the full
trading pipeline (strategies → risk → execution → portfolio).

Supports:
- Simple backtest
- Walk-forward validation
- Out-of-sample testing
"""
from __future__ import annotations
import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from core.domain.entities import MarketData, OHLCV
from core.domain.entities.order import OrderStatus
from core.domain.value_objects import Price
from core.event_bus import EventBus, MarketDataEvent
from core.interfaces.strategy_interface import IStrategy
from core.interfaces.risk_interface import IRiskEngine
from core.execution_engine import ExecutionEngine, PaperBroker
from core.portfolio_engine import PortfolioEngine
from core.decision_engine import DecisionEngine
from core.risk_engine import RiskConfig
from core.backtesting.metrics import BacktestMetrics, compute_metrics

logger = logging.getLogger(__name__)


@dataclass
class BacktestConfig:
    initial_capital: Decimal = Decimal("100000")
    slippage_bps: float = 5.0
    fee_bps: float = 10.0
    risk_free_rate: float = 0.05


@dataclass
class BacktestResult:
    metrics: BacktestMetrics
    equity_curve: list[tuple[datetime, Decimal]]
    trade_log: list[dict]
    config: BacktestConfig


class BacktestRunner:
    """
    Event-driven backtest engine.

    Feeds historical bars as MarketDataEvents through the full pipeline.
    Uses PaperBroker for simulated fills.
    """

    def __init__(
        self,
        strategies: list[IStrategy],
        risk_engine: IRiskEngine,
        config: BacktestConfig | None = None,
    ) -> None:
        self._strategies = strategies
        self._risk_engine = risk_engine
        self._config = config or BacktestConfig()

    async def run(self, market_data: MarketData) -> BacktestResult:
        """Run a single backtest over the full dataset."""
        cfg = self._config
        broker = PaperBroker(
            initial_cash=cfg.initial_capital,
            slippage_bps=cfg.slippage_bps,
            fee_bps=cfg.fee_bps,
        )
        portfolio = PortfolioEngine(cfg.initial_capital)
        execution = ExecutionEngine(broker)
        bus = EventBus()

        engine = DecisionEngine(
            event_bus=bus,
            strategies=self._strategies,
            risk_engine=self._risk_engine,
            execution_engine=execution,
            portfolio_engine=portfolio,
        )

        bars = market_data.bars
        logger.info("Backtesting %d bars for %s", len(bars), market_data.symbol)

        # MAX_LOOKBACK: use a fixed-size window for indicator computation.
        # EMA(200) needs ~600 bars to converge (k^600 ≈ 0 for α=2/201).
        # Sub-daily timeframes (4h, 1h) need more bars for the same calendar coverage.
        # Must match apps/trader_service/main.py lookback logic exactly.
        tf_val = market_data.timeframe.value if market_data.timeframe else "1d"
        MAX_LOOKBACK = 1000 if tf_val in ("4h", "1h") else 500

        for i in range(len(bars)):
            # Signal generated on closed bar i
            # Execution happens at open of bar i+1 (next bar open price)
            # Last bar: no execution possible (no next bar exists)
            window_start = max(0, i + 1 - MAX_LOOKBACK)
            slice_data = MarketData(
                symbol=market_data.symbol,
                timeframe=market_data.timeframe,
                bars=bars[window_start:i + 1],
            )

            # Set next bar open as execution price (realistic fill)
            if i + 1 < len(bars):
                next_open = bars[i + 1].open
            else:
                next_open = bars[i].close  # last bar: use close as fallback

            event = MarketDataEvent(
                market_data=slice_data,
                execution_price=next_open,
            )
            await bus.publish(event)

        equity_curve = portfolio.get_equity_curve()
        closed_trades = portfolio.closed_trades

        eq_values = [float(v) for _, v in equity_curve]
        trade_pnl_pct = [float(t.pnl_pct) for t in closed_trades]
        trade_log = [
            {
                "symbol": str(t.symbol),
                "side": t.side.value,
                "entry": float(t.entry_price.value),
                "exit": float(t.exit_price.value) if t.exit_price else None,
                "pnl": float(t.pnl),
                "pnl_pct": float(t.pnl_pct),
                "duration_s": t.duration_seconds,
                "strategy": t.strategy_id,
                "opened_at": t.opened_at.isoformat(),
                "closed_at": t.closed_at.isoformat() if t.closed_at else None,
            }
            for t in closed_trades
        ]

        metrics = compute_metrics(eq_values, trade_pnl_pct, cfg.risk_free_rate)

        return BacktestResult(
            metrics=metrics,
            equity_curve=equity_curve,
            trade_log=trade_log,
            config=cfg,
        )

    def run_sync(self, market_data: MarketData) -> BacktestResult:
        """Synchronous wrapper for run()."""
        return asyncio.run(self.run(market_data))


class WalkForwardRunner:
    """
    Walk-forward validation.

    Splits data into in-sample (IS) and out-of-sample (OOS) windows
    and runs sequential backtests, preventing overfitting.

    Timeline:
        [--IS-1--][OOS-1][--IS-2--][OOS-2] ...
    """

    def __init__(
        self,
        strategies: list[IStrategy],
        risk_engine: IRiskEngine,
        is_ratio: float = 0.7,        # 70% in-sample
        n_splits: int = 5,
        config: BacktestConfig | None = None,
    ) -> None:
        self._strategies = strategies
        self._risk_engine = risk_engine
        self._is_ratio = is_ratio
        self._n_splits = n_splits
        self._config = config or BacktestConfig()

    async def run(self, market_data: MarketData) -> list[BacktestResult]:
        """
        Run walk-forward validation.

        Returns one BacktestResult per OOS window.
        The caller should aggregate metrics across windows.
        """
        bars = market_data.bars
        total = len(bars)
        window_size = total // self._n_splits

        results: list[BacktestResult] = []

        for i in range(self._n_splits):
            start = i * window_size
            end = start + window_size if i < self._n_splits - 1 else total

            is_end = start + int((end - start) * self._is_ratio)
            oos_start = is_end

            is_bars = bars[start:is_end]
            oos_bars = bars[oos_start:end]

            if len(oos_bars) < 20:
                continue

            oos_data = MarketData(
                symbol=market_data.symbol,
                timeframe=market_data.timeframe,
                bars=oos_bars,
            )

            runner = BacktestRunner(self._strategies, self._risk_engine, self._config)
            result = await runner.run(oos_data)
            results.append(result)

            logger.info(
                "WF split %d/%d | OOS bars=%d | Return=%.2f%% | Sharpe=%.3f",
                i + 1, self._n_splits, len(oos_bars),
                result.metrics.total_return_pct,
                result.metrics.sharpe_ratio,
            )

        return results
