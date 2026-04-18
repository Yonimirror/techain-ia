"""
Historical Paper Replay — acelera la fase de validación.

Ejecuta el paper trader sobre datos históricos (hasta 3 años) barra a barra,
exactamente igual que el trader live, generando cientos de trades en minutos.

Uso:
    python -m apps.trader_service.replay                        # todas las aprobadas
    python -m apps.trader_service.replay --top 5 --years 2     # top 5, 2 años
    python -m apps.trader_service.replay --reset                # borra estado y empieza desde cero
    python -m apps.trader_service.replay --symbol BTC --tf 1d  # solo BTC 1d
"""
from __future__ import annotations
import asyncio
import json
import logging
import os
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import typer
import yaml

from core.domain.entities import MarketData, OHLCV
from core.domain.value_objects import Symbol, Timeframe, Price, Quantity
from core.event_bus import EventBus, MarketDataEvent
from core.risk_engine import RiskEngine, RiskConfig
from core.execution_engine import ExecutionEngine, PaperBroker
from core.portfolio_engine import PortfolioEngine
from core.portfolio_engine.persistence import save_state, load_state, delete_state, rebuild_portfolio, restore_risk_state
from core.decision_engine import DecisionEngine
from core.research import ResearchRepository
from core.strategies import EMACrossoverStrategy, EMACrossoverConfig
from core.strategies import RSIMeanReversionStrategy, RSIMeanReversionConfig
from core.strategies import BollingerReversionStrategy, BollingerReversionConfig
from core.strategies.rsi_smart_money import RSISmartMoneyStrategy, RSISmartMoneyConfig
from observability import configure_logging, LogLevel

logger = logging.getLogger(__name__)
app = typer.Typer(name="replay", help="Techain-IA Historical Paper Replay")

MAX_LOOKBACK = 500


def _load_env() -> None:
    env_path = Path(".env")
    if not env_path.exists():
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


def _load_risk_config() -> RiskConfig:
    path = Path("config/risk.yaml")
    if path.exists():
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        return RiskConfig.from_dict(data.get("risk", {}))
    return RiskConfig()


def _load_historical_csv(symbol: str, timeframe: str, years: float) -> MarketData | None:
    """Carga datos históricos del CSV en data/historical/."""
    csv_path = Path(f"data/historical/{symbol}_{timeframe}.csv")
    if not csv_path.exists():
        logger.warning("CSV no encontrado: %s", csv_path)
        return None

    import pandas as pd
    df = pd.read_csv(csv_path)
    df.columns = [c.lower() for c in df.columns]

    ts_col = next((c for c in df.columns if "time" in c or "date" in c), df.columns[0])
    df[ts_col] = pd.to_datetime(df[ts_col], format="mixed", utc=True, dayfirst=False)
    # Strip timezone info so comparisons with datetime.utcnow() work uniformly
    df[ts_col] = df[ts_col].dt.tz_convert(None)

    cutoff = datetime.utcnow() - timedelta(days=365 * years)
    df = df[df[ts_col] >= cutoff].reset_index(drop=True)

    if df.empty:
        return None

    sym = Symbol.of(symbol, "CRYPTO")
    tf  = Timeframe(timeframe)
    bars = []
    for _, row in df.iterrows():
        try:
            bars.append(OHLCV(
                timestamp=row[ts_col].to_pydatetime(),
                open=Price.of(float(row["open"])),
                high=Price.of(float(row["high"])),
                low=Price.of(float(row["low"])),
                close=Price.of(float(row["close"])),
                volume=Quantity.of(float(row.get("volume", 0))),
            ))
        except Exception:
            continue

    if not bars:
        return None

    return MarketData(symbol=sym, timeframe=tf, bars=bars)


def _build_strategy(row: dict):
    params = json.loads(row["params"]) if isinstance(row["params"], str) else row["params"]
    family = row["family"]
    h_id   = row["hypothesis_id"]

    if "smart_money" in h_id:
        sm_params = {k: v for k, v in params.items() if k in RSISmartMoneyConfig.__dataclass_fields__}
        return RSISmartMoneyStrategy(RSISmartMoneyConfig(**sm_params))
    elif family in ("trend", "momentum"):
        return EMACrossoverStrategy(EMACrossoverConfig(**params))
    elif family == "reversion":
        return RSIMeanReversionStrategy(RSIMeanReversionConfig(**params))
    elif family == "bollinger":
        return BollingerReversionStrategy(BollingerReversionConfig(**params))
    raise ValueError(f"Estrategia desconocida: {h_id}")


async def _replay_one(
    row: dict,
    market_data: MarketData,
    capital: Decimal,
    risk_cfg: RiskConfig,
    reset: bool,
) -> dict:
    """
    Reproduce datos históricos barra a barra para una estrategia.
    Usa exactamente el mismo motor que el trader live.
    """
    h_id = row["hypothesis_id"]
    sym  = market_data.symbol.ticker
    tf   = market_data.timeframe.value
    session_id = f"{h_id}_{sym}_{tf}"

    if reset:
        delete_state(session_id)

    saved = load_state(session_id)

    broker    = PaperBroker(initial_cash=capital, slippage_bps=5.0, fee_bps=10.0)
    risk_eng  = RiskEngine(risk_cfg)
    portfolio = PortfolioEngine(capital)
    bus       = EventBus()
    execution = ExecutionEngine(broker=broker)
    strategy  = _build_strategy(row)
    strategy._meta = {"hypothesis_id": h_id, "symbol": sym, "timeframe": tf}

    engine = DecisionEngine(
        event_bus=bus,
        strategies=[strategy],
        risk_engine=risk_eng,
        execution_engine=execution,
        portfolio_engine=portfolio,
    )

    # Restaurar estado previo
    last_bar_ts = None
    if saved:
        portfolio_restored = rebuild_portfolio(saved)
        # Transfer state
        portfolio._state = portfolio_restored._state if hasattr(portfolio_restored, '_state') else portfolio_restored
        restore_risk_state(risk_eng, saved.risk_state)
        last_bar_ts = datetime.fromisoformat(saved.last_bar_timestamp)

    bars = market_data.bars

    # Determinar qué barras son nuevas
    if last_bar_ts:
        new_start = next((i for i, b in enumerate(bars) if b.timestamp > last_bar_ts), len(bars))
        warmup = strategy.warmup_period()
        slice_start = max(0, new_start - warmup)
    else:
        slice_start = 0
        new_start   = 0

    new_bar_count = len(bars) - new_start
    if new_bar_count == 0 and saved:
        return {"skipped": True, "session_id": session_id}

    trades_before = len(list(portfolio.closed_trades))

    for i in range(slice_start, len(bars)):
        slice_data = MarketData(
            symbol=market_data.symbol,
            timeframe=market_data.timeframe,
            bars=bars[max(0, i + 1 - MAX_LOOKBACK): i + 1],
        )
        # Barras de calentamiento: procesar sin contar como señal nueva
        if i < new_start and saved:
            continue

        # Precio de ejecución = apertura de la barra siguiente (simulación realista)
        next_bar = bars[i + 1] if i + 1 < len(bars) else bars[i]
        await bus.publish(MarketDataEvent(
            market_data=slice_data,
            execution_price=next_bar.open,
        ))

    # Guardar estado
    if bars:
        save_state(session_id, portfolio, risk_eng, bars[-1].timestamp)

    # Registrar sesión en repositorio
    summary    = portfolio.summary()
    equity     = float(portfolio.state.total_equity())
    return_pct = (equity - float(capital)) / float(capital) * 100

    try:
        repo = ResearchRepository()
        repo.save_paper_session(
            hypothesis_id=h_id,
            symbol=sym,
            timeframe=tf,
            trades=summary.get("trades", 0),
            return_pct=return_pct,
            win_rate=summary.get("win_rate", 0.0),
            profit_factor=summary.get("profit_factor", 0.0),
        )
    except Exception as e:
        logger.debug("save_paper_session failed: %s", e)

    new_trades   = len(list(portfolio.closed_trades)) - trades_before
    total_trades = summary.get("trades", 0)
    win_rate     = summary.get("win_rate", 0.0)

    return {
        "skipped":     False,
        "session_id":  session_id,
        "new_trades":  new_trades,
        "total_trades": total_trades,
        "win_rate":    win_rate,
        "return_pct":  return_pct,
        "kill_switch": risk_eng.kill_switch_active,
    }


@app.command()
def run(
    top: int = typer.Option(20, help="Número de estrategias a procesar"),
    years: float = typer.Option(3.0, help="Años de histórico a procesar"),
    capital: float = typer.Option(1_000_000.0, help="Capital simulado por estrategia ($)"),
    reset: bool = typer.Option(False, "--reset", help="Borra el estado previo y empieza desde el principio"),
    symbol_filter: str = typer.Option("", "--symbol", help="Filtrar por símbolo (ej: BTC)"),
    tf_filter: str = typer.Option("", "--tf", help="Filtrar por timeframe (ej: 1d)"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """
    Reproduce hasta 3 años de datos históricos a través del motor de paper trading.
    Genera cientos de trades en minutos para acelerar la validación de estrategias.
    """
    _load_env()
    configure_logging(LogLevel.DEBUG if verbose else LogLevel.WARNING, json_output=False)

    repo     = ResearchRepository()
    approved = repo.get_approved()

    if not approved:
        typer.echo("No hay estrategias aprobadas. Ejecuta run_research primero.", err=True)
        raise typer.Exit(1)

    # Deduplicar y aplicar filtros
    seen, to_run = set(), []
    for row in approved:
        if len(to_run) >= top:
            break
        key = (row["hypothesis_id"], row["symbol"], row["timeframe"])
        if key in seen:
            continue
        seen.add(key)
        if symbol_filter and row["symbol"] != symbol_filter.upper():
            continue
        if tf_filter and row["timeframe"] != tf_filter:
            continue
        to_run.append(row)

    risk_cfg = _load_risk_config()
    init_cap = Decimal(str(capital))

    typer.echo(f"\nTechain-IA · Historical Paper Replay")
    typer.echo(f"  Estrategias : {len(to_run)}")
    typer.echo(f"  Historico   : {years} años")
    typer.echo(f"  Capital/est : ${capital:,.0f}")
    typer.echo(f"  Estado prev : {'RESET (empezar de cero)' if reset else 'continuar desde donde se dejó'}")
    typer.echo("=" * 60)

    results = []

    async def _run_all():
        for i, row in enumerate(to_run):
            h_id = row["hypothesis_id"]
            sym  = row["symbol"]
            tf   = row["timeframe"]
            label = f"[{i+1}/{len(to_run)}] {h_id} | {sym} {tf}"
            typer.echo(f"\n  {label}")

            md = _load_historical_csv(sym, tf, years)
            if md is None:
                typer.echo(f"    Sin datos para {sym} {tf} — saltando")
                continue

            typer.echo(f"    {len(md.bars)} barras disponibles ({md.bars[0].timestamp.date()} a {md.bars[-1].timestamp.date()})")

            try:
                result = await _replay_one(row, md, init_cap, risk_cfg, reset)
            except Exception as e:
                typer.echo(f"    ERROR: {e}")
                logger.exception("Replay failed for %s %s %s", h_id, sym, tf)
                continue

            if result.get("skipped"):
                typer.echo(f"    Ya al día — sin barras nuevas")
                continue

            nt  = result["new_trades"]
            tt  = result["total_trades"]
            wr  = result["win_rate"]
            rp  = result["return_pct"]
            ks  = "[KS]" if result["kill_switch"] else "[OK]"
            icon = "+" if wr >= 70 else "~" if wr >= 50 else "-"

            typer.echo(f"    +{nt} nuevos | total={tt} trades | WR={wr:.0f}% {icon} | ret={rp:+.2f}% | {ks}")
            results.append({**result, "label": f"{h_id} {sym} {tf}"})

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_run_all())
    finally:
        loop.close()

    # ── Resumen ───────────────────────────────────────────────────────────────
    typer.echo("\n" + "=" * 60)
    typer.echo("RESUMEN")
    typer.echo("=" * 60)

    if not results:
        typer.echo("Todas las estrategias están al día.")
        return

    total_new   = sum(r["new_trades"]   for r in results)
    total_all   = sum(r["total_trades"] for r in results)
    kills       = sum(1 for r in results if r["kill_switch"])
    valid_wr    = [r for r in results if r["total_trades"] >= 10]
    avg_wr      = sum(r["win_rate"] for r in valid_wr) / len(valid_wr) if valid_wr else 0
    good        = sum(1 for r in valid_wr if r["win_rate"] >= 70)

    typer.echo(f"  Trades nuevos generados  : {total_new}")
    typer.echo(f"  Trades totales acumulados: {total_all}")
    typer.echo(f"  Win Rate medio (>=10 trades): {avg_wr:.1f}%")
    typer.echo(f"  Estrategias WR >= 70%    : {good}/{len(valid_wr)}")
    typer.echo(f"  Kill switches activos    : {kills}")

    typer.echo("\nTop por Win Rate:")
    top5 = sorted([r for r in results if r["total_trades"] >= 10], key=lambda r: r["win_rate"], reverse=True)[:8]
    for r in top5:
        icon = "+" if r["win_rate"] >= 70 else "~"
        typer.echo(f"  {icon} {r['label'][:55]:<55} WR={r['win_rate']:.0f}%  trades={r['total_trades']}  ret={r['return_pct']:+.1f}%")

    typer.echo(f"\nEstado guardado en data/paper_state/")
    typer.echo("Ver resultados:")
    typer.echo("  streamlit run apps/dashboard/positions.py")


if __name__ == "__main__":
    app()
