"""
Trader Service — paper and live trading loop.

Carga automáticamente las estrategias aprobadas por el research engine
y las ejecuta en modo paper (default) o en modo live (--live).

Usage:
    python -m apps.trader_service.main                        # paper
    python -m apps.trader_service.main --live                 # live (requiere API keys en .env)
    python -m apps.trader_service.main --capital 5000 --live  # live con $5000 de allocación
    python -m apps.trader_service.main --top 1 --verbose
"""
from __future__ import annotations
import asyncio
import json
import logging
import os
import signal
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import typer
import yaml

from core.domain.entities import MarketData
from core.domain.value_objects import Symbol, Timeframe
from core.event_bus import EventBus, MarketDataEvent
from core.risk_engine import RiskEngine, RiskConfig
from core.execution_engine import ExecutionEngine, PaperBroker
from infrastructure.brokers import BinanceBroker, IBKRBroker
from core.portfolio_engine import PortfolioEngine
from core.portfolio_engine.persistence import (
    save_state, load_state, delete_state, rebuild_portfolio, restore_risk_state,
)
from core.decision_engine import DecisionEngine
from core.research import ResearchRepository
from core.strategies import EMACrossoverStrategy, EMACrossoverConfig
from core.strategies import RSIMeanReversionStrategy, RSIMeanReversionConfig
from core.strategies import BollingerReversionStrategy, BollingerReversionConfig
from core.strategies.rsi_smart_money import RSISmartMoneyStrategy, RSISmartMoneyConfig
from core.risk_engine.rebalancer import StrategyRebalancer
from core.risk_engine.sector_caps import SectorCapManager
from infrastructure.smart_money import SmartMoneyAggregator
from apps.telegram_bot.bot import TelegramNotifier
from observability import configure_logging, LogLevel


def _load_env() -> None:
    """Load .env file into environment variables if it exists."""
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


_load_env()

app = typer.Typer(name="trader", help="Techain-IA Paper Trader")
logger = logging.getLogger(__name__)

MAX_LOOKBACK = 500  # same as backtester — avoids O(n²)

# Smart Money aggregator compartido entre todas las estrategias
_smart_money: SmartMoneyAggregator | None = None

# Rebalancer compartido: carga pesos del run anterior, los aplica al actual
_rebalancer: StrategyRebalancer | None = None

# Sector cap manager compartido: enforce caps de sector/activo/total entre estrategias
_sector_caps: SectorCapManager | None = None


def _get_smart_money() -> SmartMoneyAggregator:
    global _smart_money
    if _smart_money is None:
        _smart_money = SmartMoneyAggregator()
    return _smart_money


def _get_rebalancer() -> StrategyRebalancer:
    global _rebalancer
    if _rebalancer is None:
        _rebalancer = StrategyRebalancer(
            min_weight=0.3,
            max_weight=2.0,
            lookback_trades=20,
            persist=True,
        )
    return _rebalancer


def _get_sector_caps(total_capital: float) -> SectorCapManager:
    global _sector_caps
    if _sector_caps is None:
        _sector_caps = SectorCapManager(total_capital=total_capital)
    return _sector_caps


def _load_risk_config(config_path: str = "config/risk.yaml") -> RiskConfig:
    path = Path(config_path)
    if path.exists():
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        return RiskConfig.from_dict(data.get("risk", {}))
    return RiskConfig()


def _build_strategy(row: dict):
    """Instancia la estrategia desde una fila del repositorio."""
    params = json.loads(row["params"]) if isinstance(row["params"], str) else row["params"]
    family = row["family"]
    hypothesis_id = row["hypothesis_id"]

    if "EMACrossoverStrategy" in hypothesis_id or family in ("trend", "momentum"):
        return EMACrossoverStrategy(EMACrossoverConfig(**params))
    elif "smart_money" in hypothesis_id:
        # Estrategia RSI + Smart Money (parámetros compatibles con RSISmartMoneyConfig)
        sm_params = {k: v for k, v in params.items() if k in RSISmartMoneyConfig.__dataclass_fields__}
        return RSISmartMoneyStrategy(RSISmartMoneyConfig(**sm_params))
    elif "RSIMeanReversionStrategy" in hypothesis_id or family == "reversion":
        return RSIMeanReversionStrategy(RSIMeanReversionConfig(**params))
    elif "BollingerReversionStrategy" in hypothesis_id or family == "bollinger":
        return BollingerReversionStrategy(BollingerReversionConfig(**params))

    raise ValueError(f"Cannot build strategy for hypothesis: {hypothesis_id}")


def _load_approved_strategies(top: int) -> list:
    """
    Carga las mejores estrategias aprobadas del repositorio de investigación.

    Filtrado autónomo (se ejecuta en cada arranque):
    - Excluye estrategias con kill switch en >= 80% de las últimas 10 sesiones.
    - Excluye estrategias con profit factor en papel < 1.0 tras >= 5 sesiones.
    Cualquier estrategia excluida se loggea explícitamente para trazabilidad.
    """
    repo = ResearchRepository()
    approved = repo.get_approved()

    if not approved:
        return []

    strategies = []
    seen_configs = set()

    for row in approved:
        if len(strategies) >= top:
            break

        key = (row["hypothesis_id"], row["symbol"])
        if key in seen_configs:
            continue
        seen_configs.add(key)

        # ── Filtro autónomo de estrategias muertas ────────────────────────
        should_disable, reason = repo.auto_disable_check(
            row["hypothesis_id"], row["symbol"]
        )
        if should_disable:
            typer.echo(
                f"  [!] AUTO-DISABLED: {row['hypothesis_id']} | {row['symbol']} "
                f"| {reason}"
            )
            logger.warning(
                "Strategy auto-disabled: %s %s | reason=%s",
                row["hypothesis_id"], row["symbol"], reason,
            )
            continue

        try:
            strategy = _build_strategy(row)
            strategy._meta = {
                "hypothesis_id": row["hypothesis_id"],
                "symbol": row["symbol"],
                "timeframe": row["timeframe"],
                "wf_sharpe": row["wf_sharpe_mean"],
                "tier": row.get("tier", 2),
            }
            strategies.append((strategy, row["symbol"], row["timeframe"]))
        except Exception as e:
            logger.warning("Could not build strategy %s: %s", row["hypothesis_id"], e)

    return strategies


@app.command()
def run(
    capital: float = typer.Option(100000.0, help="Capital de allocación en USD (paper) o tamaño de cartera real (live)"),
    top: int = typer.Option(3, help="Número de estrategias aprobadas a activar"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
    reset: bool = typer.Option(False, "--reset", help="Reset paper trading state (start fresh)"),
    live: bool = typer.Option(False, "--live", help="Modo LIVE: ejecuta ordenes reales (Binance crypto, IBKR equities/ETFs)"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Saltar confirmación interactiva (para systemd/CI)"),
    risk_config: str = typer.Option("config/risk.yaml", "--risk-config", help="Risk config YAML (use config/production.yaml for live)"),
) -> None:
    configure_logging(LogLevel.DEBUG if verbose else LogLevel.INFO, json_output=False)

    if live:
        typer.echo("\n" + "!" * 60)
        typer.echo("  MODO LIVE ACTIVADO")
        typer.echo("  Crypto: Binance Spot (BINANCE_API_KEY en .env)")
        typer.echo("  Equities/ETFs: IBKR TWS/Gateway (puerto 7497 paper / 7496 live)")
        typer.echo(f"  Capital: ${capital:,.2f}")
        typer.echo("!" * 60)
        if not yes:
            confirmed = typer.confirm("\n¿Confirmas que quieres operar con dinero real?", default=False)
            if not confirmed:
                typer.echo("Cancelado. Usa sin --live para paper trading.")
                raise typer.Exit(0)

    # ── Cargar estrategias aprobadas ─────────────────────────────────────────
    approved = _load_approved_strategies(top)

    if not approved:
        typer.echo(
            "No hay estrategias aprobadas en el repositorio.\n"
            "Ejecuta primero: python -m apps.research_service.run_research",
            err=True,
        )
        raise typer.Exit(1)

    mode_label = "LIVE TRADER" if live else "Paper Trader"
    typer.echo(f"\nTechain-IA {mode_label}")
    typer.echo(f"Capital: ${capital:,.2f} | Estrategias activas: {len(approved)}")
    typer.echo("")
    for strategy, symbol, timeframe in approved:
        meta = getattr(strategy, "_meta", {})
        typer.echo(
            f"  + {meta.get('hypothesis_id', strategy.strategy_id)}"
            f" | {symbol} {timeframe}"
            f" | Sharpe WF: {meta.get('wf_sharpe', 0):.3f}"
        )
    typer.echo("")

    risk_cfg = _load_risk_config(risk_config)
    initial_capital = Decimal(str(capital))

    shutdown_event = asyncio.Event()

    def _handle_signal(sig, frame):  # type: ignore
        typer.echo("\nShutdown signal received...")
        shutdown_event.set()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    async def _run_strategy(strategy, symbol_ticker: str, timeframe_str: str) -> dict:
        """Ejecuta una estrategia en modo paper con estado persistente."""
        meta = getattr(strategy, "_meta", {})
        session_id = meta.get("hypothesis_id", strategy.strategy_id) + f"_{symbol_ticker}_{timeframe_str}"
        session_id = session_id.replace("/", "_").replace(":", "_")

        # Delete state if --reset was requested
        if reset:
            delete_state(session_id)

        # Try loading previous state
        saved = load_state(session_id)

        if live:
            CRYPTO_SYMBOLS = {"BTC", "ETH", "BNB", "SOL"}
            if symbol_ticker in CRYPTO_SYMBOLS:
                broker = BinanceBroker()
                logger.info("LIVE broker (Binance) for %s %s", symbol_ticker, timeframe_str)
            else:
                broker = IBKRBroker()
                logger.info("LIVE broker (IBKR) for %s %s", symbol_ticker, timeframe_str)
        else:
            broker = PaperBroker(
                initial_cash=initial_capital,
                slippage_bps=5.0,
                fee_bps=10.0,
            )

        # ── Tier-based sizing ────────────────────────────────────────────────
        # Tier 1 (NVDA ema200, AVGO core) → 10%
        # Tier 2 (XLE, FCX, SMH, BTC 4h, etc.) → 5%
        # Tier 3 (probación, recién activados, borderline) → 2.5%
        TIER_SIZES = {1: 10.0, 2: 5.0, 3: 2.5}
        tier = meta.get("tier", 2)
        tier_size_pct = TIER_SIZES.get(tier, 5.0)

        import copy
        scaled_cfg = copy.copy(risk_cfg)
        scaled_cfg.max_position_size_pct = tier_size_pct
        scaled_cfg.max_position_per_symbol_pct = tier_size_pct

        # ── Rebalancer (ajuste dinámico sobre el tier base) ──────────────────
        # El rebalancer escala el tier size según rendimiento reciente.
        # Tier 1 con mal rendimiento reciente → baja hacia tier_size * 0.3 (min 1%)
        # Tier 1 con buen rendimiento reciente → sube hasta tier_size * 2 (max 20%)
        rebalancer = _get_rebalancer()
        strategy_key = f"{meta.get('hypothesis_id', strategy.strategy_id)}_{symbol_ticker}"
        rebalancer_weight = rebalancer.get_weight(strategy_key)
        if rebalancer_weight != 1.0:
            scaled_cfg.max_position_size_pct = max(
                1.0,
                min(tier_size_pct * rebalancer_weight, tier_size_pct * 2),
            )
            scaled_cfg.max_position_per_symbol_pct = scaled_cfg.max_position_size_pct

        logger.info(
            "Sizing for %s: tier=%d base=%.1f%% rebal=%.2fx final=%.1f%%",
            strategy_key, tier, tier_size_pct,
            rebalancer_weight, scaled_cfg.max_position_size_pct,
        )

        # ── Sector cap manager (compartido entre todas las estrategias) ───────
        total_capital = float(initial_capital) * len(approved)
        sector_caps = _get_sector_caps(total_capital)

        risk_engine = RiskEngine(scaled_cfg, sector_caps=sector_caps)

        if saved:
            portfolio = rebuild_portfolio(saved)
            restore_risk_state(risk_engine, saved.risk_state)
            last_bar_ts = datetime.fromisoformat(saved.last_bar_timestamp)
            typer.echo(f"  Estado cargado: {session_id} (last_bar={saved.last_bar_timestamp})")
        else:
            portfolio = PortfolioEngine(initial_capital)
            last_bar_ts = None

        execution = ExecutionEngine(broker)
        bus = EventBus()
        engine = DecisionEngine(
            event_bus=bus,
            strategies=[strategy],
            risk_engine=risk_engine,
            execution_engine=execution,
            portfolio_engine=portfolio,
        )

        _CRYPTO = {"BTC", "ETH", "BNB", "SOL"}
        sym = Symbol.of(symbol_ticker, "CRYPTO" if symbol_ticker in _CRYPTO else "NYSE")
        tf = Timeframe(timeframe_str)

        if symbol_ticker in _CRYPTO:
            from infrastructure.data_providers import BinanceDataProvider
            provider = BinanceDataProvider()
        else:
            from infrastructure.data_providers import YFinanceDataProvider
            provider = YFinanceDataProvider()

        lookback = 1000 if timeframe_str in ("4h", "1h") else MAX_LOOKBACK
        data = await provider.get_latest_bars(sym, tf, count=lookback)

        if len(data) == 0:
            logger.warning("No data for %s %s — skipping", symbol_ticker, timeframe_str)
            return {}

        bars = data.bars

        # Find where new bars start (after last processed bar)
        if last_bar_ts:
            new_start = len(bars)
            for idx, bar in enumerate(bars):
                if bar.timestamp > last_bar_ts:
                    new_start = idx
                    break
            # Keep enough lookback for indicator warmup
            warmup = strategy.warmup_period()
            slice_start = max(0, new_start - warmup)
        else:
            slice_start = 0
            new_start = 0

        new_bar_count = len(bars) - new_start
        if new_bar_count == 0 and saved:
            typer.echo(f"  Sin barras nuevas para {symbol_ticker} {timeframe_str} — sin cambios")
        else:
            typer.echo(f"  Procesando {new_bar_count} barras nuevas ({len(bars)} total) para {symbol_ticker} {timeframe_str}...")

        # Obtener señal Smart Money UNA vez antes del loop (datos de hoy)
        sm_signal = None
        if hasattr(strategy, "set_smart_money_context"):
            try:
                sm_signal = await _get_smart_money().get_signal(symbol_ticker)
                strategy.set_smart_money_context(sm_signal)
                logger.info(
                    "SmartMoney context set for %s: bias=%s conviction=%.2f | %s",
                    symbol_ticker, sm_signal.bias, sm_signal.conviction, sm_signal.reason,
                )
                typer.echo(
                    f"  SmartMoney {symbol_ticker}: {sm_signal.bias} "
                    f"(conviction={sm_signal.conviction:.2f}) — {sm_signal.reason}"
                )
            except Exception as e:
                logger.warning("SmartMoney signal unavailable for %s: %s", symbol_ticker, e)

        last_processed_bar = None
        try:
            for i in range(slice_start, len(bars)):
                if shutdown_event.is_set():
                    break
                slice_data = MarketData(
                    symbol=data.symbol,
                    timeframe=data.timeframe,
                    bars=bars[max(0, i + 1 - lookback): i + 1],
                )
                # Only process signals on new bars (warmup bars just build indicator state)
                if i < new_start and saved:
                    continue

                # Execution price: next bar open (same as backtester).
                # Signal is generated on bar[i].close; execution happens at bar[i+1].open.
                # This eliminates the close-vs-open bias that inflated paper metrics.
                if i + 1 < len(bars):
                    next_open = bars[i + 1].open
                else:
                    next_open = bars[i].close  # last bar: no next bar exists

                await bus.publish(MarketDataEvent(
                    market_data=slice_data,
                    execution_price=next_open,
                ))
                last_processed_bar = bars[i]
        finally:
            # Save state even if the loop is interrupted (crash safety).
            # Without this, a crash mid-loop would lose all progress and
            # risk duplicating positions on the next run.
            ts = last_processed_bar.timestamp if last_processed_bar else (bars[-1].timestamp if bars else None)
            if ts:
                save_state(session_id, portfolio, risk_engine, ts)

        # Actualizar rebalancer con los trades de este run
        closed_trades = [
            {"pnl": t.pnl}
            for t in portfolio.closed_trades
        ]
        rebalancer.update_performance(
            strategy_key=strategy_key,
            symbol=symbol_ticker,
            timeframe=timeframe_str,
            closed_trades=closed_trades,
        )

        summary = portfolio.summary()
        equity = float(portfolio.state.total_equity())
        return_pct = (equity - float(initial_capital)) / float(initial_capital) * 100
        drawdown = float(portfolio.state.drawdown())
        return {
            "hypothesis_id": meta.get("hypothesis_id", strategy.strategy_id),
            "symbol": symbol_ticker,
            "timeframe": timeframe_str,
            "total_trades": summary.get("trades", 0),
            "win_rate_pct": summary.get("win_rate", 0.0),
            "total_pnl": summary.get("total_pnl", 0.0),
            "profit_factor": summary.get("profit_factor", 0.0),
            "total_return_pct": return_pct,
            "max_drawdown_pct": drawdown,
            "kill_switch": risk_engine.kill_switch_active,
            "kill_switch_reason": risk_engine._kill_switch_reason,
        }

    async def _trading_loop() -> None:
        repo = ResearchRepository()
        results = []
        for strategy, symbol, timeframe in approved:
            if shutdown_event.is_set():
                break
            result = await _run_strategy(strategy, symbol, timeframe)
            if result:
                results.append(result)
                repo.save_paper_session(
                    hypothesis_id=result["hypothesis_id"],
                    symbol=result["symbol"],
                    timeframe=result["timeframe"],
                    total_trades=result["total_trades"],
                    win_rate_pct=result["win_rate_pct"],
                    total_return_pct=result["total_return_pct"],
                    max_drawdown_pct=result["max_drawdown_pct"],
                    profit_factor=result["profit_factor"],
                    total_pnl=result["total_pnl"],
                    bars_processed=MAX_LOOKBACK,
                    kill_switch_triggered=result["kill_switch"],
                    kill_switch_reason=result["kill_switch_reason"],
                )

        # Calcular y persistir pesos para el próximo run
        allocation = _get_rebalancer().compute_weights()
        if allocation.weights:
            logger.info("Rebalancer weights for next run: %s", allocation.weights)

        typer.echo(f"\n{'='*60}")
        typer.echo("RESULTADOS DEL PAPER TRADER")
        typer.echo(f"{'='*60}")
        for r in results:
            typer.echo(f"\n  {r['hypothesis_id']} | {r['symbol']} {r['timeframe']}")
            typer.echo(f"    Return:       {r.get('total_return_pct', 0):.2f}%")
            typer.echo(f"    Trades:       {r.get('total_trades', 0)}")
            typer.echo(f"    Win rate:     {r.get('win_rate_pct', 0):.1f}%")
            typer.echo(f"    Max drawdown: {r.get('max_drawdown_pct', 0):.2f}%")
            if r.get("kill_switch"):
                typer.echo(f"    KILL SWITCH:  {r['kill_switch_reason']}")
            strategy_key = f"{r['hypothesis_id']}_{r['symbol']}"
            weight = _get_rebalancer().get_weight(strategy_key)
            if weight != 1.0:
                typer.echo(f"    Rebal. weight: {weight:.2f}x (next run)")
            health = repo.edge_health(r["hypothesis_id"], r["symbol"])
            if health.get("status") == "ok":
                typer.echo(f"    Edge health:  OK | BT WR={health['backtest_win_rate']:.1f}% | Paper WR={health['paper_win_rate']:.1f}% | BT PF={health['backtest_profit_factor']:.2f} | Paper PF={health['paper_profit_factor']:.2f} | sessions={health['sessions']}")
            elif health.get("status") == "divergence":
                typer.echo(f"    Edge health:  DIVERGENCE! | BT WR={health['backtest_win_rate']:.1f}% | Paper WR={health['paper_win_rate']:.1f}% | BT PF={health['backtest_profit_factor']:.2f} | Paper PF={health['paper_profit_factor']:.2f} | sessions={health['sessions']}")
            else:
                typer.echo(f"    Edge health:  {health.get('status', 'unknown')} (sessions={health.get('sessions', 0)})")
        typer.echo(f"\n{'='*60}")

        # ── Telegram notifications ──────────────────────────────────────────
        notifier = TelegramNotifier()
        if notifier.enabled and results:
            try:
                await notifier.send_daily_summary(results)
                # Alert on any kill switch activations
                for r in results:
                    if r.get("kill_switch"):
                        await notifier.send_kill_switch_alert(
                            f"{r['hypothesis_id']} | {r['symbol']}: {r['kill_switch_reason']}"
                        )
                logger.info("Telegram summary sent")
            except Exception as exc:
                logger.warning("Telegram notification failed: %s", exc)

    asyncio.run(_trading_loop())


if __name__ == "__main__":
    app()
