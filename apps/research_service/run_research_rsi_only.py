#!/usr/bin/env python
"""
Research RSI Mean Reversion only (con y sin filtro EMA200).
Excluye trend, momentum, bollinger para acelerar el análisis.
"""
import asyncio
import logging
from pathlib import Path
from decimal import Decimal
from datetime import datetime, timedelta, timezone

import typer

from core.domain.value_objects import Symbol, Timeframe
from core.research.data_loader import load_multiple
from core.research.hypothesis import Hypothesis, _reversion_hypotheses
from core.research.experiment_runner import ExperimentRunner
from core.research.filters import apply_filters
from core.research.repository import ResearchRepository
from core.research.reporter import generate_report, print_report_console

logger = logging.getLogger(__name__)


def main(
    assets: list[str] = typer.Option(["BTC", "ETH"], help="Assets to analyze"),
    timeframes: list[str] = typer.Option(["1d", "4h"], help="Timeframes"),
    years: int = typer.Option(3, help="Years of historical data"),
    top: int = typer.Option(5, help="Number of strategies in report"),
    verbose: bool = typer.Option(False, "-v", help="Verbose output"),
):
    """RSI Mean Reversion research only — faster, focused on EMA200 filter comparison."""

    if verbose:
        logging.basicConfig(level=logging.DEBUG)

    typer.echo("Loading market data...")
    symbols = [Symbol(a) for a in assets]
    tfs = [Timeframe(t) for t in timeframes]

    start = datetime.now(timezone.utc) - timedelta(days=365 * years)
    end = datetime.now(timezone.utc)

    market_data_list = load_multiple(symbols, tfs, start, end)

    # Generate only reversion hypotheses
    typer.echo("Generating RSI Mean Reversion hypotheses...")
    cfg = {
        "rsi_periods": [7, 14, 21],
        "oversold": [25, 30, 35],
        "overbought": [60, 65, 70],
        "stop_loss_pcts": [3.0, 5.0],
        "enable_short": [False],
        "ema_trend_filter": [False, True],  # COMPARE!
    }
    hypotheses = _reversion_hypotheses(cfg)
    typer.echo(f"Hypotheses generated: {len(hypotheses)}")
    typer.echo(f"Total experiments: {len(hypotheses)} × {len(market_data_list)} = {len(hypotheses) * len(market_data_list)}")

    # Run experiments
    typer.echo("\nStarting experiments in parallel...")
    runner = ExperimentRunner()

    completed = [0]
    def progress(current, total, result):
        completed[0] = current
        if current % 25 == 0 or current == total:
            passed = "+" if result.passed_minimum else "."
            typer.echo(f"  [{current}/{total}] {passed} {result.hypothesis_id} | {result.symbol} | Sharpe={result.sharpe:.2f}")

    results = runner.run_all(hypotheses, market_data_list, progress_callback=progress)

    # Apply filters
    typer.echo("\nApplying robustness filters...")
    filter_results = {}
    candidates = [r for r in results if r.passed_minimum]
    typer.echo(f"Candidates for filters: {len(candidates)}/{len(results)}")

    for r in candidates:
        md = next((m for m in market_data_list if m.symbol.ticker == r.symbol and m.timeframe.value == r.timeframe), None)
        if md:
            fr = apply_filters(r, md)
            key = f"{r.hypothesis_id}_{r.symbol}_{r.timeframe}"
            filter_results[key] = fr
            status = "OK APROBADA" if fr.passed else f"FAIL ({fr.rejection_reason[:50]})"
            typer.echo(f"  {r.symbol} {r.hypothesis_id}: {status}")

    # Save results
    typer.echo("\nSaving to repository...")
    repo = ResearchRepository()
    repo.save_batch(results, filter_results)

    # Report
    approved_count = sum(1 for fr in filter_results.values() if fr.passed)
    typer.echo(f"\n{'='*60}")
    typer.echo(f"SUMMARY: {len(results)} experiments | {len(candidates)} candidates | {approved_count} approved")

    # Compare EMA on vs off
    ema_on = [fr for fr in filter_results.values() if 'ema200' in fr.hypothesis_id and fr.passed]
    ema_off = [fr for fr in filter_results.values() if 'ema200' not in fr.hypothesis_id and fr.passed]
    typer.echo(f"EMA200 filter ON: {len(ema_on)} approved")
    typer.echo(f"EMA200 filter OFF: {len(ema_off)} approved")
    typer.echo(f"{'='*60}\n")

    report = generate_report(repo, top_n=top)
    print_report_console(report)


if __name__ == "__main__":
    typer.run(main)
