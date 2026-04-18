"""
Backtest Service CLI

Usage:
    python -m apps.backtest_service.run_backtest \
        --symbol AAPL \
        --timeframe 1d \
        --start 2022-01-01 \
        --end 2023-12-31 \
        --strategy ema_crossover \
        --walk-forward \
        --monte-carlo
"""
from __future__ import annotations
import asyncio
import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import typer
import yaml

from core.domain.value_objects import Symbol, Timeframe
from core.backtesting import (
    BacktestRunner, WalkForwardRunner, BacktestConfig,
    run_monte_carlo,
)
from core.risk_engine import RiskEngine, RiskConfig
from core.strategies import EMACrossoverStrategy, RSIMeanReversionStrategy
from infrastructure.data_providers import CSVDataProvider
from observability import configure_logging, LogLevel

app = typer.Typer(name="backtest", help="Techain-IA Backtest Service")
logger = logging.getLogger(__name__)


def _load_risk_config() -> RiskConfig:
    path = Path("config/risk.yaml")
    if path.exists():
        with open(path) as f:
            data = yaml.safe_load(f)
        return RiskConfig.from_dict(data.get("risk", {}))
    return RiskConfig()


def _build_strategies(strategy_name: str) -> list:
    if strategy_name == "ema_crossover":
        return [EMACrossoverStrategy()]
    elif strategy_name == "rsi_mean_reversion":
        return [RSIMeanReversionStrategy()]
    elif strategy_name == "all":
        return [EMACrossoverStrategy(), RSIMeanReversionStrategy()]
    raise ValueError(f"Unknown strategy: {strategy_name}")


@app.command()
def run(
    symbol: str = typer.Option("AAPL", help="Symbol ticker"),
    exchange: str = typer.Option("NASDAQ", help="Exchange"),
    timeframe: str = typer.Option("1d", help="Timeframe (1m,5m,1h,4h,1d)"),
    start: str = typer.Option("2022-01-01", help="Start date YYYY-MM-DD"),
    end: str = typer.Option("2023-12-31", help="End date YYYY-MM-DD"),
    strategy: str = typer.Option("ema_crossover", help="Strategy name"),
    walk_forward: bool = typer.Option(False, "--walk-forward", help="Enable walk-forward"),
    monte_carlo: bool = typer.Option(False, "--monte-carlo", help="Enable Monte Carlo"),
    n_sims: int = typer.Option(1000, help="Monte Carlo simulations"),
    output: str = typer.Option("", help="Output JSON file path"),
    initial_capital: float = typer.Option(100000.0, help="Starting capital"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    configure_logging(LogLevel.DEBUG if verbose else LogLevel.INFO, json_output=False)

    sym = Symbol.of(symbol, exchange)
    tf = Timeframe(timeframe)
    start_dt = datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
    end_dt = datetime.fromisoformat(end).replace(tzinfo=timezone.utc)

    strategies = _build_strategies(strategy)
    risk_config = _load_risk_config()
    risk_engine = RiskEngine(risk_config)

    bt_config = BacktestConfig(
        initial_capital=Decimal(str(initial_capital)),
    )

    provider = CSVDataProvider()

    async def _run() -> None:
        market_data = await provider.get_historical(sym, tf, start_dt, end_dt)

        if len(market_data) == 0:
            typer.echo(f"No data found for {sym} {tf.value}. Check data/historical/", err=True)
            raise typer.Exit(1)

        typer.echo(f"Loaded {len(market_data)} bars for {sym} ({tf.value})")
        typer.echo(f"Running backtest: {strategy} | {start} to {end}")

        results = []

        if walk_forward:
            runner = WalkForwardRunner(strategies, risk_engine, config=bt_config)
            wf_results = await runner.run(market_data)
            for i, r in enumerate(wf_results):
                typer.echo(f"\n--- Walk-Forward Split {i+1} ---")
                typer.echo(str(r.metrics))
                results.append(r)
        else:
            runner = BacktestRunner(strategies, risk_engine, bt_config)
            result = await runner.run(market_data)
            typer.echo("\n--- Backtest Results ---")
            typer.echo(str(result.metrics))
            results.append(result)

        if monte_carlo and results:
            all_pnl = []
            for r in results:
                all_pnl.extend(r.trade_log)
            pnl_pct = [t["pnl_pct"] for t in all_pnl if t["pnl_pct"] is not None]

            if pnl_pct:
                mc = run_monte_carlo(pnl_pct, initial_equity=initial_capital, n_simulations=n_sims)
                typer.echo(f"\n--- Monte Carlo ({n_sims} simulations) ---")
                typer.echo(f"Sharpe (mean):      {mc.sharpe_mean:.3f} ± {mc.sharpe_std:.3f}")
                typer.echo(f"Sharpe (5th pct):   {mc.sharpe_p5:.3f}")
                typer.echo(f"Max DD (mean):      {mc.max_drawdown_mean:.2f}%")
                typer.echo(f"Max DD (95th pct):  {mc.max_drawdown_p95:.2f}%")
                typer.echo(f"Return (mean):      {mc.total_return_mean:+.2f}%")
                typer.echo(f"Return (5th pct):   {mc.total_return_p5:+.2f}%")
                typer.echo(f"P(loss):            {mc.probability_of_loss:.1f}%")

        if output and results:
            out_data = [
                {
                    "metrics": vars(r.metrics),
                    "trades": r.trade_log,
                }
                for r in results
            ]
            Path(output).write_text(json.dumps(out_data, indent=2, default=str))
            typer.echo(f"\nResults saved to {output}")

    asyncio.run(_run())


if __name__ == "__main__":
    app()
