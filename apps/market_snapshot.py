"""
Market Snapshot — estado actual del mercado para todos los pares activos.

Descarga datos en tiempo real de Binance, calcula indicadores y muestra
el estado actual de cada estrategia aprobada.

Usage:
    python -m apps.market_snapshot
    python -m apps.market_snapshot --pairs BTC ETH SOL --timeframes 1d 4h
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import typer

# ── Bootstrap ────────────────────────────────────────────────────────────────

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

_load_env()

# ── Imports after env ─────────────────────────────────────────────────────────

from core.domain.value_objects import Symbol, Timeframe
from core.market_regime.detector import RegimeDetector
from core.strategies.indicators import rsi, ema, atr, adx
from infrastructure.data_providers import BinanceDataProvider

app = typer.Typer(name="snapshot", add_completion=False)

# Pares y timeframes activos según estrategias aprobadas
DEFAULT_PAIRS = ["BTC", "ETH", "SOL"]
DEFAULT_TIMEFRAMES = ["1d", "4h"]

# Thresholds de señal por estrategia (del config/strategies)
# Formato: (par, tf) → (rsi_period, oversold, overbought)
STRATEGY_PARAMS: dict[tuple[str, str], tuple[int, float, float]] = {
    ("BTC", "1d"): (7,  30.0, 60.0),
    ("BTC", "4h"): (14, 25.0, 60.0),
    ("ETH", "4h"): (14, 35.0, 60.0),
    ("SOL", "1d"): (7,  35.0, 60.0),
    ("ETH", "1d"): (7,  35.0, 60.0),
}
DEFAULT_RSI_PARAMS = (14, 30.0, 70.0)


def _signal_label(rsi_val: float, oversold: float, overbought: float) -> str:
    if rsi_val <= oversold:
        return "*** OVERSOLD — SEÑAL LARGA ***"
    if rsi_val <= oversold + 5:
        return f"  cerca oversold (faltan {rsi_val - oversold:.1f} pts)"
    if rsi_val >= overbought:
        return "  OVERBOUGHT — posible salida"
    return "  neutral"


def _vol_arrow(atr_pct: float) -> str:
    if atr_pct > 5.0:
        return "^^"
    if atr_pct > 3.0:
        return "^"
    if atr_pct < 1.0:
        return "v"
    return " "


async def _snapshot(
    pairs: list[str],
    timeframes: list[str],
    count: int,
) -> None:
    provider = BinanceDataProvider()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    print()
    print("=" * 72)
    print(f"  MARKET SNAPSHOT — {now}")
    print("=" * 72)

    for tf in timeframes:
        print(f"\n  [ {tf} ]")
        print(f"  {'Par':<5} {'Precio':>12} {'RSI':>6} {'EMA200':>12} {'ATR%':>6} {'ADX':>6}  Régimen       Señal")
        print("  " + "-" * 70)

        for pair in pairs:
            sym = Symbol.of(pair, "CRYPTO")
            try:
                data = await provider.get_latest_bars(sym, Timeframe(tf), count=count)
            except Exception as e:
                print(f"  {pair:<5}  ERROR: {e}")
                continue

            bars = data.bars
            if len(bars) < 50:
                print(f"  {pair:<5}  datos insuficientes ({len(bars)} barras)")
                continue

            closes = [float(b.close.value) for b in bars]
            highs  = [float(b.high.value)  for b in bars]
            lows   = [float(b.low.value)   for b in bars]

            rsi_period, os_thresh, ob_thresh = STRATEGY_PARAMS.get(
                (pair, tf), DEFAULT_RSI_PARAMS
            )

            rsi_vals  = rsi(closes, rsi_period)
            ema200    = ema(closes, 200)
            atr_vals  = atr(highs, lows, closes, 14)
            adx_vals  = adx(highs, lows, closes, 14)

            cur_rsi   = next((v for v in reversed(rsi_vals)  if v is not None), None)
            cur_ema   = next((v for v in reversed(ema200)    if v is not None), None)
            cur_atr   = next((v for v in reversed(atr_vals)  if v is not None), None)
            cur_adx   = next((v for v in reversed(adx_vals)  if v is not None), None)
            cur_price = closes[-1]

            if cur_rsi is None:
                print(f"  {pair:<5}  RSI insuficiente (necesita {rsi_period} barras)")
                continue

            atr_pct = (cur_atr / cur_price * 100) if cur_atr else 0.0
            ema_str = f"{cur_ema:>12.2f}" if cur_ema else "     N/A    "
            adx_str = f"{cur_adx:>6.1f}" if cur_adx else "   N/A"

            # Precio vs EMA200
            ema_pos = ""
            if cur_ema:
                if cur_price > cur_ema * 1.001:
                    ema_pos = " (sobre EMA)"
                elif cur_price < cur_ema * 0.999:
                    ema_pos = " (bajo EMA)"

            regime = RegimeDetector.detect(data)
            regime_str = str(regime.trend.value) + "/" + str(regime.volatility.value) if regime else "N/A"

            signal = _signal_label(cur_rsi, os_thresh, ob_thresh)
            vol_arrow = _vol_arrow(atr_pct)

            print(
                f"  {pair:<5} {cur_price:>12.2f} "
                f"{cur_rsi:>6.1f} "
                f"{ema_str} "
                f"{atr_pct:>5.1f}%{vol_arrow} "
                f"{adx_str}  "
                f"{regime_str:<14}  {signal}"
            )

            # Línea extra de contexto si está cerca o en señal
            if cur_rsi <= os_thresh + 10:
                dist_ema = ((cur_price / cur_ema) - 1) * 100 if cur_ema else 0
                print(
                    f"        RSI{rsi_period} threshold={os_thresh:.0f} | "
                    f"precio vs EMA200: {dist_ema:+.1f}%{ema_pos}"
                )

    print()
    print("=" * 72)
    print("  Thresholds activos:")
    for (pair, tf), (period, os, ob) in STRATEGY_PARAMS.items():
        if tf in timeframes and pair in pairs:
            print(f"    {pair} {tf}: RSI{period} oversold<{os:.0f}  overbought>{ob:.0f}")
    print("=" * 72)
    print()


@app.command()
def run(
    pairs: list[str] = typer.Option(DEFAULT_PAIRS, "--pairs", "-p", help="Pares a monitorizar"),
    timeframes: list[str] = typer.Option(DEFAULT_TIMEFRAMES, "--timeframes", "-t", help="Timeframes"),
    count: int = typer.Option(500, "--count", help="Barras históricas a descargar"),
) -> None:
    """Muestra el estado actual del mercado: RSI, EMA200, ATR, ADX y régimen."""
    asyncio.run(_snapshot(pairs, timeframes, count))


if __name__ == "__main__":
    app()
