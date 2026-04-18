"""
Análisis de correlación entre estrategias activas del portfolio.

Fuentes de datos reales:
  - data/research/experiments.db  → parámetros de estrategias activas
  - data/historical/<SYMBOL>_<TF>.csv → OHLCV histórico

El script simula cada estrategia vectorizadamente para obtener una equity curve
indexada por fechas de mercado reales (no timestamps de procesamiento del paper trader).

Uso:
    python strategy_correlation.py [--symbol AVGO] [--all]

    --symbol BTC     → analiza solo estrategias de BTC
    --all            → analiza todas las estrategias activas (42 actualmente)
    Sin flags        → agrupa por símbolo y muestra correlaciones inter-símbolo

Output:
    - Matriz de correlación Pearson y Spearman entre retornos diarios
    - Clusters de redundancia (correlación > threshold)
    - Top pares más/menos correlacionados
    - correlation_pearson.csv, correlation_spearman.csv
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

try:
    from scipy.cluster.hierarchy import fcluster, linkage
    from scipy.spatial.distance import squareform
    SCIPY_AVAILABLE = True
except ImportError:
    SCIPY_AVAILABLE = False
    print("[WARN] scipy no instalado — clustering deshabilitado. pip install scipy")

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------
DB_PATH = Path("data/research/experiments.db")
HIST_DIR = Path("data/historical")
REDUNDANCY_THRESHOLD = 0.85


# ---------------------------------------------------------------------------
# INDICADORES
# ---------------------------------------------------------------------------

def calc_rsi(close: pd.Series, period: int) -> pd.Series:
    """RSI con suavizado de Wilder (equivalente a EMA con alpha=1/period)."""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def calc_ema(close: pd.Series, period: int) -> pd.Series:
    return close.ewm(span=period, adjust=False).mean()


# ---------------------------------------------------------------------------
# SIMULACIÓN VECTORIZADA
# ---------------------------------------------------------------------------

def simulate_strategy(df: pd.DataFrame, params: dict) -> pd.Series:
    """
    Simula RSI Mean Reversion sobre OHLCV y devuelve retorno diario indexado
    por fecha de mercado.

    Modelo de ejecución:
      - Entrada: cierre de la barra donde RSI < oversold (y EMA filter si aplica)
      - Salida:  cierre de la barra donde RSI > overbought O stop_loss se dispara
      - Sin slippage ni comisiones (correlación no requiere PnL exacto)
    """
    rsi_period = int(params.get("rsi_period", 14))
    oversold = float(params.get("oversold_threshold", 30))
    overbought = float(params.get("overbought_threshold", 70))
    stop_loss_pct = float(params.get("stop_loss_pct", 5)) / 100
    ema_filter = bool(params.get("ema_trend_filter", False))
    ema_period = int(params.get("ema_trend_period", 200))

    close = df["close"].astype(float)
    rsi = calc_rsi(close, rsi_period)
    ema200 = calc_ema(close, ema_period) if ema_filter else None

    # Estado de la simulación
    in_position = False
    entry_price = 0.0
    daily_returns = pd.Series(0.0, index=df.index)

    for i in range(1, len(df)):
        c = float(close.iloc[i])
        r = float(rsi.iloc[i]) if not np.isnan(rsi.iloc[i]) else 50.0

        if not in_position:
            ema_ok = True
            if ema_filter and ema200 is not None:
                e = float(ema200.iloc[i])
                ema_ok = c > e if not np.isnan(e) else True
            if r < oversold and ema_ok:
                in_position = True
                entry_price = c
        else:
            # Evaluar salida
            loss = (entry_price - c) / entry_price if entry_price > 0 else 0
            if r > overbought or loss >= stop_loss_pct:
                ret = (c - entry_price) / entry_price
                daily_returns.iloc[i] = ret
                in_position = False
                entry_price = 0.0

    return daily_returns


# ---------------------------------------------------------------------------
# CARGA DE DATOS
# ---------------------------------------------------------------------------

def load_active_strategies(symbol_filter: str | None = None) -> list[dict]:
    """Lee estrategias activas de experiments.db."""
    conn = sqlite3.connect(DB_PATH)
    if symbol_filter:
        rows = conn.execute(
            "SELECT hypothesis_id, symbol, timeframe, params, profit_factor "
            "FROM experiments WHERE passed_filters=1 AND symbol=? "
            "ORDER BY symbol, profit_factor DESC",
            (symbol_filter,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT hypothesis_id, symbol, timeframe, params, profit_factor "
            "FROM experiments WHERE passed_filters=1 "
            "ORDER BY symbol, profit_factor DESC"
        ).fetchall()
    conn.close()

    strategies = []
    for hyp_id, symbol, timeframe, params_json, pf in rows:
        # smart_money behaves like RSI+EMA in backtest (SM context not injected)
        # but differs in live/paper → simulation would be misleading, skip
        if hyp_id.startswith("smart_money"):
            continue
        strategies.append({
            "id": hyp_id,
            "symbol": symbol,
            "timeframe": timeframe,
            "params": json.loads(params_json),
            "profit_factor": pf,
            "label": f"{symbol}_{hyp_id}",
        })
    return strategies


def load_ohlcv(symbol: str, timeframe: str) -> pd.DataFrame | None:
    """Carga CSV histórico para símbolo+timeframe."""
    path = HIST_DIR / f"{symbol}_{timeframe}.csv"
    if not path.exists():
        print(f"  [WARN] CSV no encontrado: {path}")
        return None
    df = pd.read_csv(path, index_col="timestamp")
    df.index = pd.to_datetime(df.index, format="mixed", utc=True).tz_convert(None)
    df = df.sort_index()
    return df


# ---------------------------------------------------------------------------
# ANÁLISIS DE CORRELACIÓN
# ---------------------------------------------------------------------------

def build_returns_matrix(strategies: list[dict]) -> pd.DataFrame:
    """Construye DataFrame de retornos diarios (un columna por estrategia)."""
    # Agrupar por (symbol, timeframe) para no recargar CSVs
    ohlcv_cache: dict[tuple, pd.DataFrame] = {}
    series_list = []

    for s in strategies:
        key = (s["symbol"], s["timeframe"])
        if key not in ohlcv_cache:
            df = load_ohlcv(s["symbol"], s["timeframe"])
            if df is None:
                continue
            ohlcv_cache[key] = df

        df = ohlcv_cache[key]
        try:
            rets = simulate_strategy(df, s["params"])
            rets.name = s["label"]
            series_list.append(rets)
        except Exception as e:
            print(f"  [ERROR] {s['label']}: {e}")

    if not series_list:
        raise RuntimeError("No se pudo simular ninguna estrategia.")

    # Resamplear todo a diario para poder combinar 1d y 4h
    daily_list = []
    for s in series_list:
        # Quitar duplicados en el índice (pueden venir del CSV)
        s = s[~s.index.duplicated(keep="last")]
        # Resamplear a 1D sumando retornos del día
        daily = s.resample("1D").sum()
        daily_list.append(daily)

    return pd.concat(daily_list, axis=1)


def correlation_analysis(returns: pd.DataFrame) -> dict:
    # Rellenar NaN con 0: días sin posición → retorno 0
    returns_filled = returns.fillna(0)
    pearson = returns_filled.corr(method="pearson")
    spearman = returns_filled.corr(method="spearman")
    return {"pearson": pearson, "spearman": spearman, "data": returns_filled}


def find_redundant_clusters(corr_matrix: pd.DataFrame, threshold: float) -> list:
    if not SCIPY_AVAILABLE:
        return []
    dist_matrix = 1 - corr_matrix.abs()
    dist_matrix = (dist_matrix + dist_matrix.T) / 2
    dist_arr = dist_matrix.values.copy()
    np.fill_diagonal(dist_arr, 0)
    dist_matrix = pd.DataFrame(dist_arr, index=dist_matrix.index, columns=dist_matrix.columns)
    condensed = squareform(dist_arr, checks=False)
    Z = linkage(condensed, method="average")
    cluster_labels = fcluster(Z, t=1 - threshold, criterion="distance")
    clusters: dict[int, list] = {}
    for name, cid in zip(corr_matrix.index, cluster_labels):
        clusters.setdefault(cid, []).append(name)
    return [m for m in clusters.values() if len(m) > 1]


# ---------------------------------------------------------------------------
# INFORME
# ---------------------------------------------------------------------------

def print_report(
    corr_result: dict,
    redundant_clusters: list,
    strategies: list[dict],
    threshold: float,
):
    pf_map = {s["label"]: s["profit_factor"] for s in strategies}

    print("\n" + "=" * 70)
    print("MATRIZ DE CORRELACIÓN (Pearson, retornos por trade)")
    print("=" * 70)
    print(corr_result["pearson"].round(3).to_string())

    print("\n" + "=" * 70)
    print("MATRIZ DE CORRELACIÓN (Spearman, rank-based)")
    print("=" * 70)
    print(corr_result["spearman"].round(3).to_string())

    print("\n" + "=" * 70)
    print(f"CLUSTERS DE REDUNDANCIA (Pearson > {threshold})")
    print("=" * 70)
    if not redundant_clusters:
        print("  Ningún cluster redundante. Las estrategias están diversificadas.")
    else:
        for i, cluster in enumerate(redundant_clusters, 1):
            best = max(cluster, key=lambda x: pf_map.get(x, 0))
            print(f"\n  Cluster #{i} — {len(cluster)} estrategias (misma apuesta):")
            for name in sorted(cluster, key=lambda x: -pf_map.get(x, 0)):
                tag = " << MEJOR PF" if name == best else " << DESACTIVAR"
                print(f"    PF={pf_map.get(name, 0):.2f}  {name}{tag}")

    print("\n" + "=" * 70)
    print("TOP 10 PARES MAS CORRELACIONADOS")
    print("=" * 70)
    pearson = corr_result["pearson"]
    n = len(pearson)
    mask = np.triu(np.ones((n, n), dtype=bool), k=1)
    upper = pearson.where(mask)
    pairs = upper.stack().dropna().sort_values(ascending=False).head(10)
    for (a, b), val in pairs.items():
        flag = "  !! REDUNDANTE" if val > threshold else ""
        pf_a = pf_map.get(a, 0)
        pf_b = pf_map.get(b, 0)
        print(f"  {val:+.3f}  [{pf_a:.2f}] {a}  <->  [{pf_b:.2f}] {b}{flag}")

    print("\n" + "=" * 70)
    print("TOP 5 PARES MENOS CORRELACIONADOS (diversificacion real)")
    print("=" * 70)
    pairs_low = upper.stack().dropna().sort_values(ascending=True).head(5)
    for (a, b), val in pairs_low.items():
        pf_a = pf_map.get(a, 0)
        pf_b = pf_map.get(b, 0)
        print(f"  {val:+.3f}  [{pf_a:.2f}] {a}  <->  [{pf_b:.2f}] {b}")


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", help="Filtrar por símbolo (ej: AVGO, BTC)")
    parser.add_argument("--all", action="store_true", help="Todas las estrategias activas")
    parser.add_argument("--threshold", type=float, default=REDUNDANCY_THRESHOLD,
                        help=f"Umbral correlación (default {REDUNDANCY_THRESHOLD})")
    args = parser.parse_args()

    symbol = args.symbol.upper() if args.symbol else None
    threshold = args.threshold

    print("=" * 70)
    print("ANÁLISIS DE CORRELACIÓN DE ESTRATEGIAS")
    print("=" * 70)

    strategies = load_active_strategies(symbol)
    if not strategies:
        print(f"No hay estrategias activas{' para ' + symbol if symbol else ''}.")
        return

    print(f"\nEstrategias a analizar: {len(strategies)}")
    for s in strategies:
        print(f"  [{s['profit_factor']:.2f}] {s['label']} ({s['timeframe']})")

    print("\nSimulando equity curves...")
    returns = build_returns_matrix(strategies)

    # Solo columnas con al menos un trade
    returns = returns.loc[:, (returns != 0).any()]
    print(f"  Columnas con señales: {len(returns.columns)}")
    print(f"  Rango temporal: {returns.index.min().date()} -> {returns.index.max().date()}")

    if len(returns.columns) < 2:
        print("Se necesitan al menos 2 estrategias con datos para calcular correlación.")
        return

    corr_result = correlation_analysis(returns)
    redundant_clusters = find_redundant_clusters(corr_result["pearson"], threshold)

    print_report(corr_result, redundant_clusters, strategies, threshold)

    # Guardar
    corr_result["pearson"].to_csv("correlation_pearson.csv")
    corr_result["spearman"].to_csv("correlation_spearman.csv")
    print("\n  Guardado: correlation_pearson.csv, correlation_spearman.csv")


if __name__ == "__main__":
    main()
