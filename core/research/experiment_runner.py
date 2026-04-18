"""
Experiment Runner — ejecuta backtests en paralelo para todas las hipótesis.

Por cada hipótesis:
  1. Instancia la estrategia con los parámetros dados
  2. Corre backtest completo con fees y ejecución realista
  3. Aplica walk-forward
  4. Devuelve ExperimentResult estructurado

Usa multiprocessing para paralelizar — en un PC normal
puede correr ~200-500 experimentos por minuto.
"""
from __future__ import annotations
import asyncio
import logging
import multiprocessing as mp
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

from core.domain.entities import MarketData
from core.research.hypothesis import Hypothesis
from core.backtesting import BacktestRunner, WalkForwardRunner, BacktestConfig, BacktestMetrics
from core.risk_engine import RiskEngine, RiskConfig
from core.strategies import EMACrossoverStrategy, EMACrossoverConfig
from core.strategies import RSIMeanReversionStrategy, RSIMeanReversionConfig
from core.strategies import BollingerReversionStrategy, BollingerReversionConfig
from core.strategies.rsi_smart_money import RSISmartMoneyStrategy, RSISmartMoneyConfig

logger = logging.getLogger(__name__)


@dataclass
class ExperimentResult:
    hypothesis_id: str
    family: str
    symbol: str
    timeframe: str
    params: dict

    # Métricas del backtest completo
    sharpe: float
    max_drawdown: float
    profit_factor: float
    win_rate: float
    expectancy: float
    total_trades: int
    total_return_pct: float

    # Walk-forward (métricas OOS promediadas)
    wf_sharpe_mean: float
    wf_sharpe_min: float
    wf_consistency: float    # % de splits con Sharpe > 0

    # Metadatos
    ran_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    error: str | None = None

    @property
    def is_valid(self) -> bool:
        return self.error is None and self.total_trades >= 10

    @property
    def passed_minimum(self) -> bool:
        """Filtro mínimo antes de pasar a filtros de robustez."""
        return (
            self.is_valid
            and self.sharpe > 0.3
            and self.profit_factor > 1.1
            and self.max_drawdown < 30.0
            and self.total_trades >= 10
        )


def run_experiment(args: tuple) -> ExperimentResult:
    """
    Función ejecutada por cada proceso worker.
    Recibe (hypothesis, market_data_dict, backtest_config_dict).
    """
    hypothesis, md_dict, cfg_dict = args

    try:
        market_data = _rebuild_market_data(md_dict)
        strategy = _build_strategy(hypothesis)
        risk_engine = RiskEngine(RiskConfig(
            max_trades_per_day=999,
            max_total_exposure_pct=95.0,
            # Disable edge degradation scaling in research — we want to measure
            # the strategy's raw edge, not a self-modifying adaptive version.
            # EdgeMonitor is only meaningful in live/paper trading.
            edge_min_win_rate=0.0,
            edge_min_profit_factor=0.0,
        ))
        bt_config = BacktestConfig(
            initial_capital=Decimal(str(cfg_dict["initial_capital"])),
            slippage_bps=cfg_dict["slippage_bps"],
            fee_bps=cfg_dict["fee_bps"],
        )

        # Backtest completo
        runner = BacktestRunner([strategy], risk_engine, bt_config)
        result = asyncio.run(runner.run(market_data))
        m = result.metrics

        # Walk-forward (n_splits=3 gives 0/33/67/100% consistency values;
        # with n_splits=4 the only values passing 60% are 75% and 100%)
        wf_runner = WalkForwardRunner(
            [strategy], risk_engine,
            is_ratio=0.7, n_splits=3,
            config=bt_config,
        )
        wf_results = asyncio.run(wf_runner.run(market_data))
        wf_sharpes = [r.metrics.sharpe_ratio for r in wf_results if r.metrics.total_trades >= 1]
        wf_sharpe_mean = sum(wf_sharpes) / len(wf_sharpes) if wf_sharpes else 0.0
        wf_sharpe_min = min(wf_sharpes) if wf_sharpes else 0.0
        wf_consistency = sum(1 for s in wf_sharpes if s > 0) / len(wf_sharpes) if wf_sharpes else 0.0

        return ExperimentResult(
            hypothesis_id=hypothesis.hypothesis_id,
            family=hypothesis.family,
            symbol=md_dict["symbol"],
            timeframe=md_dict["timeframe"],
            params=hypothesis.params,
            sharpe=m.sharpe_ratio,
            max_drawdown=m.max_drawdown_pct,
            profit_factor=m.profit_factor,
            win_rate=m.win_rate_pct,
            expectancy=m.expectancy,
            total_trades=m.total_trades,
            total_return_pct=m.total_return_pct,
            wf_sharpe_mean=wf_sharpe_mean,
            wf_sharpe_min=wf_sharpe_min,
            wf_consistency=wf_consistency,
        )

    except Exception as e:
        logger.error("Experiment failed %s: %s", hypothesis.hypothesis_id, e)
        return ExperimentResult(
            hypothesis_id=hypothesis.hypothesis_id,
            family=hypothesis.family,
            symbol=md_dict.get("symbol", "?"),
            timeframe=md_dict.get("timeframe", "?"),
            params=hypothesis.params,
            sharpe=0.0, max_drawdown=0.0, profit_factor=0.0,
            win_rate=0.0, expectancy=0.0, total_trades=0,
            total_return_pct=0.0, wf_sharpe_mean=0.0,
            wf_sharpe_min=0.0, wf_consistency=0.0,
            error=str(e),
        )


class ExperimentRunner:
    """
    Orquesta la ejecución paralela de todos los experimentos.

    Uso:
        runner = ExperimentRunner(config)
        results = runner.run_all(hypotheses, market_data_list)
    """

    def __init__(self, config: dict | None = None) -> None:
        self._config = config or {
            "initial_capital": 100000,
            "slippage_bps": 5.0,
            "fee_bps": 10.0,
        }
        self._n_workers = max(1, mp.cpu_count() - 1)

    def run_all(
        self,
        hypotheses: list[Hypothesis],
        market_data_list: list[MarketData],
        progress_callback=None,
    ) -> list[ExperimentResult]:
        """
        Ejecuta todos los experimentos para todos los activos en paralelo.

        Returns lista de ExperimentResult ordenados por Sharpe descendente.
        """
        # Construir lista de tareas: cada hipótesis × cada activo
        tasks = []
        for md in market_data_list:
            md_dict = _serialize_market_data(md)
            for h in hypotheses:
                tasks.append((h, md_dict, self._config))

        total = len(tasks)
        logger.info(
            "Running %d experiments (%d hypotheses × %d assets) on %d workers",
            total, len(hypotheses), len(market_data_list), self._n_workers,
        )

        results = []
        with mp.Pool(processes=self._n_workers) as pool:
            for i, result in enumerate(pool.imap_unordered(run_experiment, tasks, chunksize=4)):
                results.append(result)
                if progress_callback:
                    progress_callback(i + 1, total, result)
                elif (i + 1) % 50 == 0 or (i + 1) == total:
                    logger.info("Progress: %d/%d experiments", i + 1, total)

        # Ordenar por Sharpe walk-forward descendente
        results.sort(key=lambda r: r.wf_sharpe_mean, reverse=True)
        valid = [r for r in results if r.is_valid]
        logger.info(
            "Completed: %d total | %d valid | %d passed minimum filter",
            total, len(valid), sum(1 for r in valid if r.passed_minimum),
        )
        return results


# ── helpers de serialización (necesarios para multiprocessing) ──────────────

def _serialize_market_data(md: MarketData) -> dict:
    """Convierte MarketData a dict serializable para multiprocessing."""
    return {
        "symbol": md.symbol.ticker,
        "exchange": md.symbol.exchange,
        "timeframe": md.timeframe.value,
        "bars": [
            {
                "timestamp": b.timestamp.isoformat(),
                "open": float(b.open.value),
                "high": float(b.high.value),
                "low": float(b.low.value),
                "close": float(b.close.value),
                "volume": float(b.volume.value),
            }
            for b in md.bars
        ],
    }


def _rebuild_market_data(d: dict) -> MarketData:
    """Reconstruye MarketData desde dict serializado."""
    from core.domain.value_objects import Symbol, Timeframe
    from core.domain.entities import MarketData, OHLCV
    from core.domain.value_objects import Price, Quantity
    from datetime import datetime

    symbol = Symbol.of(d["symbol"], d.get("exchange", "UNKNOWN"))
    timeframe = Timeframe(d["timeframe"])
    bars = [
        OHLCV(
            timestamp=datetime.fromisoformat(b["timestamp"]),
            open=Price.of(b["open"]),
            high=Price.of(b["high"]),
            low=Price.of(b["low"]),
            close=Price.of(b["close"]),
            volume=Quantity.of(b["volume"]),
        )
        for b in d["bars"]
    ]
    return MarketData(symbol=symbol, timeframe=timeframe, bars=bars)


def _build_strategy(hypothesis: Hypothesis):
    """Instancia la estrategia correcta con los parámetros de la hipótesis."""
    if hypothesis.strategy_class == "EMACrossoverStrategy":
        config = EMACrossoverConfig(**hypothesis.params)
        return EMACrossoverStrategy(config)
    elif hypothesis.strategy_class == "RSIMeanReversionStrategy":
        config = RSIMeanReversionConfig(**hypothesis.params)
        return RSIMeanReversionStrategy(config)
    elif hypothesis.strategy_class == "BollingerReversionStrategy":
        config = BollingerReversionConfig(**hypothesis.params)
        return BollingerReversionStrategy(config)
    elif hypothesis.strategy_class == "RSISmartMoneyStrategy":
        # In backtest, no Smart Money signal is injected → behaves like RSI+EMA200 baseline.
        # The SM filter only activates in paper/live when set_smart_money_context() is called.
        config = RSISmartMoneyConfig(**hypothesis.params)
        return RSISmartMoneyStrategy(config)
    raise ValueError(f"Unknown strategy class: {hypothesis.strategy_class}")
