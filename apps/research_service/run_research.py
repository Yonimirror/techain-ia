"""
Research Service CLI

Uso básico:
    python -m apps.research_service.run_research

Con opciones:
    python -m apps.research_service.run_research --assets BTC ETH SPY --timeframes 1d 4h
    python -m apps.research_service.run_research --report-only
"""
from __future__ import annotations
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import typer
import yaml

from core.domain.value_objects import Timeframe
from core.research import (
    load_multiple, generate_hypotheses,
    ExperimentRunner, apply_filters,
    ResearchRepository, generate_report, print_report_console,
)

app = typer.Typer(name="research", help="Techain-IA Research Engine")
logger = logging.getLogger(__name__)


def _load_config(path: str = "config/research.yaml") -> dict:
    p = Path(path)
    if p.exists():
        with open(p) as f:
            return yaml.safe_load(f) or {}
    return {}


@app.command()
def run(
    assets: list[str] = typer.Option(["BTC", "ETH", "SPY"], help="Activos a analizar"),
    timeframes: list[str] = typer.Option(["1d", "4h"], help="Timeframes"),
    years: int = typer.Option(3, help="Años de histórico a usar"),
    report_only: bool = typer.Option(False, "--report-only", help="Solo genera informe sin correr experimentos"),
    top: int = typer.Option(5, help="Número de estrategias en el informe"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
    config_file: str = typer.Option("config/research.yaml", "--config", help="YAML config file path"),
) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    config = _load_config(config_file)
    repo = ResearchRepository()

    # ── Solo informe ──────────────────────────────────────────────────────────
    if report_only:
        typer.echo("Generando informe desde repositorio existente...")
        report = generate_report(repo, top_n=top)
        print_report_console(report)
        return

    # ── Carga de datos ───────────────────────────────────────────────────────
    typer.echo(f"Cargando datos: {assets} | timeframes: {timeframes}")
    tfs = [Timeframe(tf) for tf in timeframes]
    asset_pairs = [(ticker, _exchange(ticker)) for ticker in assets]
    start = datetime.now(timezone.utc) - timedelta(days=365 * years)

    market_data_list = load_multiple(asset_pairs, tfs, start=start)

    if not market_data_list:
        typer.echo("ERROR: No se pudieron cargar datos. Verifica conexión a internet.", err=True)
        raise typer.Exit(1)

    typer.echo(f"Datos cargados: {len(market_data_list)} series")
    for md in market_data_list:
        typer.echo(f"  {md.symbol.ticker} {md.timeframe.value}: {len(md)} barras")

    # ── Generación de hipótesis ───────────────────────────────────────────────
    hypotheses = generate_hypotheses(config.get("hypotheses"))
    typer.echo(f"\nHipótesis generadas: {len(hypotheses)}")
    typer.echo(f"Experimentos totales: {len(hypotheses)} × {len(market_data_list)} = {len(hypotheses) * len(market_data_list)}")

    # ── Ejecución paralela ────────────────────────────────────────────────────
    typer.echo("\nIniciando experimentos en paralelo...")
    runner = ExperimentRunner(config.get("backtest", {}))

    completed = [0]
    def progress(current, total, result):
        completed[0] = current
        if current % 25 == 0 or current == total:
            passed = "+" if result.passed_minimum else "."
            typer.echo(f"  [{current}/{total}] {passed} {result.hypothesis_id} | {result.symbol} | Sharpe={result.sharpe:.2f}")

    results = runner.run_all(hypotheses, market_data_list, progress_callback=progress)

    # ── Aplicar filtros de robustez ───────────────────────────────────────────
    typer.echo("\nAplicando filtros de robustez...")
    filter_results = {}
    candidates = [r for r in results if r.passed_minimum]
    typer.echo(f"Candidatos para filtros: {len(candidates)}/{len(results)}")

    for r in candidates:
        md = next((m for m in market_data_list if m.symbol.ticker == r.symbol and m.timeframe.value == r.timeframe), None)
        if md:
            fr = apply_filters(r, md, config.get("filters"))
            key = f"{r.hypothesis_id}_{r.symbol}_{r.timeframe}"  # include asset+timeframe to avoid collisions
            filter_results[key] = fr
            status = "OK APROBADA" if fr.passed else f"FAIL RECHAZADA ({fr.rejection_reason[:60]})"
            typer.echo(f"  {r.symbol} {r.hypothesis_id}: {status}")

    # ── Guardar resultados ────────────────────────────────────────────────────
    typer.echo("\nGuardando en repositorio...")
    repo.save_batch(results, filter_results)

    # ── Informe final ─────────────────────────────────────────────────────────
    approved_count = sum(1 for fr in filter_results.values() if fr.passed)
    typer.echo(f"\n{'='*60}")
    typer.echo(f"RESUMEN: {len(results)} experimentos | {len(candidates)} candidatos | {approved_count} aprobados")
    typer.echo(f"{'='*60}\n")

    report = generate_report(repo, top_n=top)
    print_report_console(report)

    typer.echo(f"\nInforme guardado en: data/research/reports/")
    typer.echo(f"Datos en: data/research/experiments.csv")


def _exchange(ticker: str) -> str:
    crypto = {"BTC", "ETH", "BNB", "SOL", "XRP"}
    return "CRYPTO" if ticker in crypto else "NYSE"


if __name__ == "__main__":
    app()
