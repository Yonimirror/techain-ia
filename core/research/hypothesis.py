"""
Generador de hipótesis — define el espacio de estrategias a explorar.

Tres familias:
  1. Tendencia    — EMA crossover con períodos variables
  2. Reversión    — RSI mean reversion con umbrales variables
  3. Momentum     — Rate of Change con períodos variables

Cada hipótesis es un dict de parámetros que el ExperimentRunner
convierte en una estrategia concreta y testea.
"""
from __future__ import annotations
from dataclasses import dataclass
from itertools import product
from typing import Any


@dataclass(frozen=True)
class Hypothesis:
    """Una hipótesis es una estrategia + parámetros específicos a testear."""
    family: str          # "trend" | "reversion" | "momentum"
    strategy_class: str  # nombre de la clase de estrategia
    params: dict         # parámetros concretos
    hypothesis_id: str   # identificador único

    def __repr__(self) -> str:
        return f"Hypothesis({self.family} | {self.hypothesis_id})"


def generate_hypotheses(config: dict | None = None) -> list[Hypothesis]:
    """
    Genera todas las hipótesis a testear según la configuración.

    Returns lista de Hypothesis ordenadas de menor a mayor complejidad.
    """
    cfg = config or _default_config()
    hypotheses: list[Hypothesis] = []

    if cfg.get("trend", {}).get("enabled", True):
        hypotheses.extend(_trend_hypotheses(cfg["trend"]))

    if cfg.get("reversion", {}).get("enabled", True):
        hypotheses.extend(_reversion_hypotheses(cfg["reversion"]))

    if cfg.get("momentum", {}).get("enabled", True):
        hypotheses.extend(_momentum_hypotheses(cfg["momentum"]))

    if cfg.get("bollinger", {}).get("enabled", True):
        hypotheses.extend(_bollinger_hypotheses(cfg.get("bollinger", {})))

    if cfg.get("smart_money", {}).get("enabled", False):
        hypotheses.extend(_smart_money_hypotheses(cfg.get("smart_money", {})))

    return hypotheses


def _trend_hypotheses(cfg: dict) -> list[Hypothesis]:
    """EMA crossover: fast x slow con distintas combinaciones."""
    fast_periods = cfg.get("fast_periods", [5, 9, 12])
    slow_periods = cfg.get("slow_periods", [21, 50, 100])
    min_strength = cfg.get("min_strength", [0.2, 0.3, 0.4])
    stop_loss_pcts = cfg.get("stop_loss_pcts", [3.0, 5.0])
    enable_short_opts = cfg.get("enable_short", [False])

    hypotheses = []
    for fast, slow, strength, sl, short in product(fast_periods, slow_periods, min_strength, stop_loss_pcts, enable_short_opts):
        if fast >= slow:
            continue
        params = {
            "fast_period": fast,
            "slow_period": slow,
            "min_strength_threshold": strength,
            "stop_loss_pct": sl,
            "enable_short": short,
        }
        short_tag = "_short" if short else ""
        h_id = f"trend_ema_{fast}_{slow}_s{int(strength*100)}_sl{int(sl)}{short_tag}"
        hypotheses.append(Hypothesis(
            family="trend",
            strategy_class="EMACrossoverStrategy",
            params=params,
            hypothesis_id=h_id,
        ))
    return hypotheses


def _reversion_hypotheses(cfg: dict) -> list[Hypothesis]:
    """RSI mean reversion: períodos y umbrales variables, con/sin filtro EMA200."""
    rsi_periods = cfg.get("rsi_periods", [7, 14, 21])
    oversold = cfg.get("oversold", [25, 30, 35])
    overbought = cfg.get("overbought", [65, 70, 75])
    stop_loss_pcts = cfg.get("stop_loss_pcts", [3.0, 5.0])
    enable_short_opts = cfg.get("enable_short", [False])
    ema_filter_opts = cfg.get("ema_trend_filter", [False, True])

    hypotheses = []
    for period, os_val, ob_val, sl, short, ema_filter in product(
        rsi_periods, oversold, overbought, stop_loss_pcts, enable_short_opts, ema_filter_opts
    ):
        if os_val >= ob_val:
            continue
        params = {
            "rsi_period": period,
            "oversold_threshold": float(os_val),
            "overbought_threshold": float(ob_val),
            "stop_loss_pct": sl,
            "enable_short": short,
            "ema_trend_filter": ema_filter,
            "ema_trend_period": 200,
        }
        short_tag = "_short" if short else ""
        ema_tag = "_ema200" if ema_filter else ""
        h_id = f"reversion_rsi{period}_os{os_val}_ob{ob_val}_sl{int(sl)}{short_tag}{ema_tag}"
        hypotheses.append(Hypothesis(
            family="reversion",
            strategy_class="RSIMeanReversionStrategy",
            params=params,
            hypothesis_id=h_id,
        ))
    return hypotheses


def _momentum_hypotheses(cfg: dict) -> list[Hypothesis]:
    """
    Momentum: Rate of Change — entra cuando el precio sube X% en N días.
    Usamos EMACrossoverStrategy con períodos muy cortos como proxy de momentum.
    """
    fast_periods = cfg.get("fast_periods", [3, 5, 7])
    slow_periods = cfg.get("slow_periods", [10, 15, 20])
    stop_loss_pcts = cfg.get("stop_loss_pcts", [3.0, 5.0])
    enable_short_opts = cfg.get("enable_short", [False])

    hypotheses = []
    for fast, slow, sl, short in product(fast_periods, slow_periods, stop_loss_pcts, enable_short_opts):
        if fast >= slow:
            continue
        params = {
            "fast_period": fast,
            "slow_period": slow,
            "min_strength_threshold": 0.1,
            "stop_loss_pct": sl,
            "enable_short": short,
        }
        short_tag = "_short" if short else ""
        h_id = f"momentum_ema_{fast}_{slow}_sl{int(sl)}{short_tag}"
        hypotheses.append(Hypothesis(
            family="momentum",
            strategy_class="EMACrossoverStrategy",
            params=params,
            hypothesis_id=h_id,
        ))
    return hypotheses


def _bollinger_hypotheses(cfg: dict) -> list[Hypothesis]:
    """Bollinger Band mean reversion con RSI como confirmación."""
    bb_periods = cfg.get("bb_period", [15, 20, 25])
    bb_stds = cfg.get("bb_std", [1.5, 2.0, 2.5])
    rsi_periods = cfg.get("rsi_period", [7, 14])
    oversold = cfg.get("rsi_oversold", [35, 40, 45])
    overbought = cfg.get("rsi_overbought", [55, 60, 65])
    stop_loss_pcts = cfg.get("stop_loss_pcts", [3.0, 5.0])
    enable_short_opts = cfg.get("enable_short", [False])

    hypotheses = []
    for bb_p, bb_s, rsi_p, os_val, ob_val, sl, short in product(
        bb_periods, bb_stds, rsi_periods, oversold, overbought, stop_loss_pcts, enable_short_opts,
    ):
        if os_val >= ob_val:
            continue
        params = {
            "bb_period": bb_p,
            "bb_std": bb_s,
            "rsi_period": rsi_p,
            "rsi_oversold": float(os_val),
            "rsi_overbought": float(ob_val),
            "stop_loss_pct": sl,
            "enable_short": short,
        }
        short_tag = "_short" if short else ""
        h_id = f"bollinger_bb{bb_p}_std{int(bb_s*10)}_rsi{rsi_p}_os{os_val}_ob{ob_val}_sl{int(sl)}{short_tag}"
        hypotheses.append(Hypothesis(
            family="bollinger",
            strategy_class="BollingerReversionStrategy",
            params=params,
            hypothesis_id=h_id,
        ))
    return hypotheses


def _smart_money_hypotheses(cfg: dict) -> list[Hypothesis]:
    """
    RSI + Smart Money: igual que RSI Mean Reversion con EMA200 forzado.

    En backtest el Smart Money signal es None → comportamiento idéntico a RSI+EMA200.
    En paper trading el SmartMoneyAggregator inyecta el señal real antes de generate_signals().
    El hypothesis_id contiene "smart_money" para que el paper trader construya RSISmartMoneyStrategy.
    """
    rsi_periods = cfg.get("rsi_periods", [7, 14])
    oversold = cfg.get("oversold", [30, 35])
    overbought = cfg.get("overbought", [60, 65])
    stop_loss_pcts = cfg.get("stop_loss_pcts", [3.0, 5.0])

    hypotheses = []
    for period, os_val, ob_val, sl in product(rsi_periods, oversold, overbought, stop_loss_pcts):
        if os_val >= ob_val:
            continue
        params = {
            "rsi_period": period,
            "oversold_threshold": float(os_val),
            "overbought_threshold": float(ob_val),
            "stop_loss_pct": sl,
            "ema_trend_filter": True,       # siempre activo — EMA200 es obligatorio
            "ema_trend_period": 200,
            "smart_money_enabled": True,    # activado en paper, sin efecto en backtest
            "smart_money_lookback_hours": 24,
            "skip_on_bearish_smart_money": True,
            "base_position_pct": 8.0,
            "max_position_pct": 15.0,
            "min_position_pct": 3.0,
        }
        h_id = f"smart_money_rsi{period}_os{os_val}_ob{ob_val}_sl{int(sl)}_ema200"
        hypotheses.append(Hypothesis(
            family="smart_money",
            strategy_class="RSISmartMoneyStrategy",
            params=params,
            hypothesis_id=h_id,
        ))
    return hypotheses


def _default_config() -> dict:
    return {
        "trend": {
            "enabled": True,
            "fast_periods": [5, 9, 12],
            "slow_periods": [21, 50, 100],
            "min_strength": [0.2, 0.3, 0.4],
            "stop_loss_pcts": [3.0, 5.0],
        },
        "reversion": {
            "enabled": True,
            "rsi_periods": [7, 14, 21],
            "oversold": [25, 30, 35],
            "overbought": [65, 70, 75],
            "stop_loss_pcts": [3.0, 5.0],
        },
        "momentum": {
            "enabled": True,
            "fast_periods": [3, 5, 7],
            "slow_periods": [10, 15, 20],
            "stop_loss_pcts": [3.0, 5.0],
        },
        "bollinger": {
            "enabled": True,
            "bb_period": [15, 20, 25],
            "bb_std": [1.5, 2.0, 2.5],
            "rsi_period": [7, 14],
            "rsi_oversold": [35, 40, 45],
            "rsi_overbought": [55, 60, 65],
            "stop_loss_pcts": [3.0, 5.0],
            "enable_short": [False],
        },
    }
