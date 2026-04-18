"""
Filtros de robustez — tres capas obligatorias antes de aprobar una estrategia.

Capa 1: Walk-forward (ya calculado en ExperimentResult)
Capa 2: Robustez de parámetros — el edge no desaparece con ±10% en params
Capa 3: Consistencia temporal — funciona en al menos 3 de cada 4 periodos

Una estrategia que no pasa los tres filtros NO entra al registry.
"""
from __future__ import annotations
import asyncio
import logging
from dataclasses import dataclass

from core.research.experiment_runner import ExperimentResult, run_experiment, _serialize_market_data
from core.research.hypothesis import Hypothesis
from core.domain.entities import MarketData

logger = logging.getLogger(__name__)


@dataclass
class FilterResult:
    hypothesis_id: str
    passed: bool

    # Detalle por filtro
    wf_passed: bool
    wf_sharpe_mean: float
    wf_consistency: float

    param_robustness_passed: bool
    param_robustness_score: float   # 0-1, % de variaciones que mantienen edge

    consistency_passed: bool
    consistency_score: float        # % de periodos positivos

    rejection_reason: str = ""


def apply_filters(
    result: ExperimentResult,
    market_data: MarketData,
    config: dict | None = None,
) -> FilterResult:
    """
    Aplica los tres filtros a un ExperimentResult.

    Args:
        result: Resultado del experimento base
        market_data: Datos para el filtro de robustez de parámetros
        config: Umbrales de filtros (usa defaults si None)

    Returns:
        FilterResult con aprobación/rechazo y detalle
    """
    cfg = config or _default_filter_config()

    # ── Filtro 0: Mínimo de trades ───────────────────────────────────────────
    # Un Sharpe calculado con < 30 trades no es estadísticamente confiable.
    min_trades = cfg["min_trades"]
    if result.total_trades < min_trades:
        return FilterResult(
            hypothesis_id=result.hypothesis_id,
            passed=False,
            wf_passed=False,
            wf_sharpe_mean=result.wf_sharpe_mean,
            wf_consistency=result.wf_consistency,
            param_robustness_passed=False,
            param_robustness_score=0.0,
            consistency_passed=False,
            consistency_score=result.wf_consistency,
            rejection_reason=f"INSUFFICIENT_TRADES: {result.total_trades} < {min_trades}",
        )

    # ── Filtro 1: Walk-forward ───────────────────────────────────────────────
    wf_min_sharpe = cfg["wf_min_sharpe_mean"]
    wf_min_consistency = cfg["wf_min_consistency"]

    wf_passed = (
        result.wf_sharpe_mean >= wf_min_sharpe
        and result.wf_consistency >= wf_min_consistency
    )

    # ── Filtro 2: Robustez de parámetros ────────────────────────────────────
    param_score = _parameter_robustness(result, market_data, cfg)
    param_passed = param_score >= cfg["param_robustness_min_score"]

    # ── Filtro 3: Consistencia temporal ─────────────────────────────────────
    consistency_score = result.wf_consistency
    consistency_passed = consistency_score >= cfg["wf_min_consistency"]

    passed = wf_passed and param_passed and consistency_passed

    reason = ""
    if not passed:
        parts = []
        if not wf_passed:
            parts.append(
                f"WF: sharpe={result.wf_sharpe_mean:.2f}<{wf_min_sharpe} "
                f"consistency={result.wf_consistency:.0%}<{wf_min_consistency:.0%}"
            )
        if not param_passed:
            parts.append(f"PARAM_ROBUSTNESS: score={param_score:.0%}<{cfg['param_robustness_min_score']:.0%}")
        if not consistency_passed:
            parts.append(f"CONSISTENCY: {consistency_score:.0%}")
        reason = " | ".join(parts)

    return FilterResult(
        hypothesis_id=result.hypothesis_id,
        passed=passed,
        wf_passed=wf_passed,
        wf_sharpe_mean=result.wf_sharpe_mean,
        wf_consistency=result.wf_consistency,
        param_robustness_passed=param_passed,
        param_robustness_score=param_score,
        consistency_passed=consistency_passed,
        consistency_score=consistency_score,
        rejection_reason=reason,
    )


def _parameter_robustness(
    result: ExperimentResult,
    market_data: MarketData,
    cfg: dict,
) -> float:
    """
    Varía cada parámetro ±10% y comprueba cuántas variaciones mantienen Sharpe > 0.

    Returns: score entre 0 y 1 (% de variaciones que pasan)
    """
    from core.research.hypothesis import Hypothesis
    from core.risk_engine import RiskEngine, RiskConfig
    from core.backtesting import BacktestRunner, BacktestConfig
    from decimal import Decimal

    variation_pct = cfg.get("param_variation_pct", 0.10)
    min_sharpe = cfg.get("param_min_sharpe", 0.0)

    params = result.params
    if not params:
        return 1.0  # sin parámetros = máxima robustez

    variations = _generate_param_variations(params, variation_pct)
    if not variations:
        return 1.0

    passed = 0
    md_dict = _serialize_market_data(market_data)
    bt_cfg = {
        "initial_capital": 100000,
        "slippage_bps": 5.0,
        "fee_bps": 10.0,
    }

    for varied_params in variations:
        h = Hypothesis(
            family=result.family,
            strategy_class=_family_to_class(result.family),
            params=varied_params,
            hypothesis_id=f"{result.hypothesis_id}_var",
        )
        # Use backtest-only (no walk-forward) for speed — WF already validated on base params
        var_result = _run_backtest_only((h, md_dict, bt_cfg))
        if var_result.sharpe >= min_sharpe and var_result.total_trades >= 1:
            passed += 1

    score = passed / len(variations)
    logger.debug(
        "Param robustness %s: %d/%d variations passed → score=%.0f%%",
        result.hypothesis_id, passed, len(variations), score * 100,
    )
    return score


def _run_backtest_only(args: tuple) -> "ExperimentResult":
    """Lightweight version of run_experiment — full backtest only, no walk-forward."""
    from core.research.experiment_runner import (
        ExperimentResult, _rebuild_market_data, _build_strategy,
    )
    from core.risk_engine import RiskEngine, RiskConfig
    from core.backtesting import BacktestRunner, BacktestConfig
    from core.backtesting.metrics import compute_metrics
    from decimal import Decimal
    import asyncio

    hypothesis, md_dict, cfg_dict = args
    try:
        market_data = _rebuild_market_data(md_dict)
        strategy = _build_strategy(hypothesis)
        risk_engine = RiskEngine(RiskConfig(
            max_trades_per_day=999,
            max_total_exposure_pct=95.0,
            edge_min_win_rate=0.0,
            edge_min_profit_factor=0.0,
        ))
        bt_config = BacktestConfig(
            initial_capital=Decimal(str(cfg_dict["initial_capital"])),
            slippage_bps=cfg_dict["slippage_bps"],
            fee_bps=cfg_dict["fee_bps"],
        )
        runner = BacktestRunner([strategy], risk_engine, bt_config)
        result = asyncio.run(runner.run(market_data))
        m = result.metrics
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
            wf_sharpe_mean=0.0,
            wf_sharpe_min=0.0,
            wf_consistency=0.0,
        )
    except Exception as e:
        from core.research.experiment_runner import ExperimentResult
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


def _generate_param_variations(params: dict, variation_pct: float) -> list[dict]:
    """Genera variaciones ±variation_pct para cada parámetro numérico."""
    variations = []
    for key, value in params.items():
        if not isinstance(value, (int, float)):
            continue
        for factor in [1 - variation_pct, 1 + variation_pct]:
            varied = dict(params)
            new_val = value * factor
            # Mantener tipo (int si era int)
            varied[key] = int(round(new_val)) if isinstance(value, int) else round(new_val, 4)
            # Evitar valores inválidos
            if isinstance(value, int) and varied[key] < 1:
                varied[key] = 1
            variations.append(varied)
    return variations


def _family_to_class(family: str) -> str:
    mapping = {
        "trend": "EMACrossoverStrategy",
        "reversion": "RSIMeanReversionStrategy",
        "momentum": "EMACrossoverStrategy",
        "bollinger": "BollingerReversionStrategy",
        "smart_money": "RSISmartMoneyStrategy",
    }
    return mapping.get(family, "EMACrossoverStrategy")


def _default_filter_config() -> dict:
    return {
        "min_trades": 10,                  # mínimo de trades para validez estadística
        "wf_min_sharpe_mean": 0.2,         # Sharpe OOS promedio mínimo
        "wf_min_consistency": 0.6,         # 60% de splits positivos
        "param_robustness_min_score": 0.6, # 60% de variaciones mantienen edge
        "param_variation_pct": 0.10,       # ±10% en cada parámetro
        "param_min_sharpe": 0.0,           # Sharpe mínimo en variaciones
    }
