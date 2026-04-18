"""
Techain-IA Dashboard — Monitor visual del agente de trading.
Lanzar con: streamlit run apps/dashboard/app.py
"""
import json
import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

# ── Config ────────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Techain-IA",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

PAPER_STATE_DIR = Path("data/paper_state")
RESEARCH_DB = Path("data/research/experiments.db")
RESEARCH_LOG = Path("data/logs/research_full.log")
REBALANCER_JSON = Path("data/rebalancer/performance.json")

# ── Auto-refresh ──────────────────────────────────────────────────────────────
st.sidebar.title("⚙️ Techain-IA")
refresh_secs = st.sidebar.selectbox("Auto-refresh", [0, 15, 30, 60], index=2)
if refresh_secs:
    st.markdown(
        f'<meta http-equiv="refresh" content="{refresh_secs}">',
        unsafe_allow_html=True,
    )
    st.sidebar.caption(f"Actualizando cada {refresh_secs}s")

st.sidebar.markdown("---")
page = st.sidebar.radio(
    "Sección",
    ["📊 Paper Trader", "🔬 Research", "⚖️ Rebalancer", "📋 Log en vivo"],
)

# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def load_paper_states() -> list[dict]:
    states = []
    for f in sorted(PAPER_STATE_DIR.glob("*.json")):
        try:
            d = json.loads(f.read_text())
            d["_file"] = f.stem
            states.append(d)
        except Exception:
            pass
    return states


def load_research_db() -> pd.DataFrame | None:
    if not RESEARCH_DB.exists():
        return None
    conn = sqlite3.connect(RESEARCH_DB)
    df = pd.read_sql("SELECT * FROM experiments", conn)
    conn.close()
    return df


def color_edge(val: str) -> str:
    if "OK" in str(val):
        return "color: #00c853"
    if "DIVERGENCE" in str(val):
        return "color: #ff6d00"
    return ""


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: PAPER TRADER
# ══════════════════════════════════════════════════════════════════════════════
if page == "📊 Paper Trader":
    st.title("📊 Paper Trader — Estado en tiempo real")

    states = load_paper_states()
    if not states:
        st.warning("No hay estados de paper trading guardados todavía.")
        st.stop()

    # ── KPIs globales ──────────────────────────────────────────────────────
    total_initial = sum(float(s["initial_capital"]) for s in states)
    total_equity = sum(float(s["cash"]) for s in states)
    total_return_pct = (total_equity - total_initial) / total_initial * 100
    active_ks = sum(1 for s in states if s["risk_state"].get("kill_switch_active"))

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Capital inicial", f"${total_initial:,.0f}")
    k2.metric(
        "Equity actual",
        f"${total_equity:,.0f}",
        delta=f"{total_return_pct:+.2f}%",
        delta_color="normal",
    )
    k3.metric("Estrategias activas", len(states))
    k4.metric(
        "Kill switches activos",
        active_ks,
        delta_color="inverse" if active_ks else "off",
    )

    st.markdown("---")

    # ── Tabla de estrategias ───────────────────────────────────────────────
    st.subheader("Estrategias")
    rows = []
    all_equity_curves = []

    for s in states:
        sid = s["session_id"]
        cash = float(s["cash"])
        initial = float(s["initial_capital"])
        peak = float(s["peak_equity"])
        ret_pct = (cash - initial) / initial * 100
        dd_pct = (peak - cash) / peak * 100 if peak > 0 else 0
        closed = s["closed_trades"]
        wins = sum(1 for t in closed if float(t.get("pnl", 0)) > 0)
        wr = wins / len(closed) * 100 if closed else 0
        rs = s["risk_state"]
        ks = "🔴 ACTIVO" if rs.get("kill_switch_active") else "🟢 OK"
        consec = rs.get("consecutive_losses", 0)

        rows.append({
            "Estrategia": sid,
            "Equity": f"${cash:,.0f}",
            "Retorno": f"{ret_pct:+.2f}%",
            "Max DD": f"{dd_pct:.2f}%",
            "Trades": len(closed),
            "Win Rate": f"{wr:.0f}%",
            "Pérd. consec.": consec,
            "Kill Switch": ks,
        })

        # Equity curve
        for pt in s.get("equity_curve", []):
            all_equity_curves.append({
                "timestamp": pt["timestamp"],
                "equity": float(pt["equity"]),
                "estrategia": sid,
            })

    df_table = pd.DataFrame(rows)
    st.dataframe(df_table, use_container_width=True, hide_index=True)

    # ── Equity curves ──────────────────────────────────────────────────────
    if all_equity_curves:
        st.subheader("Equity Curves")
        df_eq = pd.DataFrame(all_equity_curves)
        df_eq["timestamp"] = pd.to_datetime(df_eq["timestamp"])
        df_eq = df_eq.sort_values("timestamp")

        fig = px.line(
            df_eq,
            x="timestamp",
            y="equity",
            color="estrategia",
            title="Evolución del capital por estrategia",
            labels={"equity": "Equity ($)", "timestamp": ""},
        )
        fig.update_layout(legend_title="Estrategia", height=400)
        st.plotly_chart(fig, use_container_width=True)

    # ── Detalle por estrategia ─────────────────────────────────────────────
    st.subheader("Detalle de trades")
    selected = st.selectbox("Selecciona estrategia", [s["session_id"] for s in states])
    sel_state = next(s for s in states if s["session_id"] == selected)
    closed = sel_state["closed_trades"]

    if closed:
        df_trades = pd.DataFrame(closed)
        numeric_cols = ["pnl", "entry_price", "exit_price", "quantity"]
        for col in numeric_cols:
            if col in df_trades.columns:
                df_trades[col] = pd.to_numeric(df_trades[col], errors="coerce")

        col_a, col_b = st.columns(2)
        with col_a:
            if "pnl" in df_trades.columns:
                fig_pnl = px.bar(
                    df_trades.reset_index(),
                    x="index",
                    y="pnl",
                    color=df_trades["pnl"].apply(lambda x: "Win" if x > 0 else "Loss"),
                    color_discrete_map={"Win": "#00c853", "Loss": "#d32f2f"},
                    title="PnL por trade",
                    labels={"index": "Trade #", "pnl": "PnL ($)"},
                )
                st.plotly_chart(fig_pnl, use_container_width=True)

        with col_b:
            if "pnl" in df_trades.columns:
                cumulative = df_trades["pnl"].cumsum()
                fig_cum = px.area(
                    cumulative,
                    title="PnL acumulado",
                    labels={"value": "PnL ($)", "index": "Trade #"},
                )
                fig_cum.update_traces(line_color="#1976d2", fillcolor="rgba(25,118,210,0.15)")
                st.plotly_chart(fig_cum, use_container_width=True)

        st.dataframe(df_trades, use_container_width=True, hide_index=True)
    else:
        st.info("Sin trades cerrados todavía.")


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: RESEARCH
# ══════════════════════════════════════════════════════════════════════════════
elif page == "🔬 Research":
    st.title("🔬 Research Engine — Análisis de estrategias")

    df = load_research_db()
    if df is None or df.empty:
        st.warning("Base de datos de research no encontrada.")
        st.stop()

    total = len(df)
    approved = df["passed_filters"].sum() if "passed_filters" in df.columns else 0
    rejected = total - approved

    # ── Progreso ───────────────────────────────────────────────────────────
    st.subheader("Progreso del research")
    # Check if research is running by reading log
    progress_pct = total / 6552 if total <= 6552 else 1.0
    st.progress(min(progress_pct, 1.0))

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Experimentos totales", f"{total:,} / 6,552")
    k2.metric("Aprobados", int(approved), delta=f"{approved/total*100:.1f}%" if total else "")
    k3.metric("Rechazados", int(rejected))
    k4.metric("Tasa de aprobación", f"{approved/total*100:.1f}%" if total else "0%")

    st.markdown("---")

    # ── Top estrategias aprobadas ──────────────────────────────────────────
    approved_df = df[df["passed_filters"] == 1].copy() if "passed_filters" in df.columns else pd.DataFrame()

    if not approved_df.empty:
        st.subheader("Top estrategias aprobadas")
        approved_df = approved_df.sort_values("sharpe", ascending=False)

        display_cols = ["hypothesis_id", "symbol", "timeframe", "family",
                        "sharpe", "wf_sharpe_mean", "win_rate", "total_trades",
                        "wf_consistency", "profit_factor"]
        display_cols = [c for c in display_cols if c in approved_df.columns]
        top_df = approved_df[display_cols].head(20).copy()

        for col in ["sharpe", "wf_sharpe_mean", "profit_factor"]:
            if col in top_df.columns:
                top_df[col] = top_df[col].round(2)
        if "win_rate" in top_df.columns:
            top_df["win_rate"] = top_df["win_rate"].round(1)

        st.dataframe(top_df, use_container_width=True, hide_index=True)

        # ── Sharpe por activo ──────────────────────────────────────────
        col_a, col_b = st.columns(2)
        with col_a:
            fig = px.box(
                approved_df,
                x="symbol",
                y="sharpe",
                color="symbol",
                title="Distribución Sharpe por activo",
                points="all",
            )
            st.plotly_chart(fig, use_container_width=True)

        with col_b:
            if "family" in approved_df.columns:
                fig2 = px.histogram(
                    approved_df,
                    x="family",
                    color="symbol",
                    barmode="group",
                    title="Aprobados por familia y activo",
                )
                st.plotly_chart(fig2, use_container_width=True)

    else:
        st.info("Ninguna estrategia aprobada todavía. El research está en curso.")

    # ── Distribución general ───────────────────────────────────────────────
    st.subheader("Distribución de Sharpe — todos los experimentos")
    if "sharpe" in df.columns:
        fig_hist = px.histogram(
            df[df["sharpe"].between(-10, 15)],
            x="sharpe",
            color="symbol" if "symbol" in df.columns else None,
            nbins=60,
            title="Distribución de Sharpe ratio (filtrado ±10)",
            labels={"sharpe": "Sharpe Ratio", "count": "Experimentos"},
        )
        fig_hist.add_vline(x=0.5, line_dash="dash", line_color="orange", annotation_text="Mínimo")
        st.plotly_chart(fig_hist, use_container_width=True)

    # ── Mapa de calor: familia × activo ───────────────────────────────────
    if "family" in df.columns and "symbol" in df.columns:
        st.subheader("Sharpe medio por familia y activo")
        pivot = df.groupby(["family", "symbol"])["sharpe"].mean().unstack(fill_value=0).round(2)
        fig_heat = px.imshow(
            pivot,
            text_auto=True,
            color_continuous_scale="RdYlGn",
            title="Sharpe medio — familia × activo",
            zmin=-2, zmax=3,
        )
        st.plotly_chart(fig_heat, use_container_width=True)


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: REBALANCER
# ══════════════════════════════════════════════════════════════════════════════
elif page == "⚖️ Rebalancer":
    st.title("⚖️ Strategy Rebalancer — Asignación de capital")

    if not REBALANCER_JSON.exists():
        st.info("El rebalancer no tiene datos todavía. Se alimenta con los trades del paper trader.")
        st.stop()

    data = json.loads(REBALANCER_JSON.read_text())
    strategies = {k: v for k, v in data.items() if k != "__weights__"}
    weights_meta = data.get("__weights__", {})

    if weights_meta:
        st.caption(f"Último rebalanceo: {weights_meta.get('rebalanced_at', 'N/A')} | {weights_meta.get('reason', '')}")

    if not strategies:
        st.info("Sin datos de estrategias en el rebalancer.")
        st.stop()

    rows = []
    for key, s in strategies.items():
        weight = weights_meta.get("weights", {}).get(key, 1.0)
        rows.append({
            "Estrategia": key,
            "Trades": s.get("recent_trades", 0),
            "Win Rate": f"{float(s.get('win_rate_pct', s.get('recent_wins', 0)) or 0):.0f}%",
            "PnL": f"${float(s.get('recent_pnl', 0)):+,.0f}",
            "Peso": round(weight, 2),
            "Capital relativo": f"{'▲' if weight > 1 else '▼' if weight < 1 else '='} {weight:.2f}x",
        })

    df_reb = pd.DataFrame(rows).sort_values("Peso", ascending=False)
    st.dataframe(df_reb, use_container_width=True, hide_index=True)

    if rows:
        fig = px.bar(
            df_reb,
            x="Estrategia",
            y="Peso",
            color="Peso",
            color_continuous_scale="RdYlGn",
            title="Peso de capital por estrategia (1.0 = neutral)",
        )
        fig.add_hline(y=1.0, line_dash="dash", line_color="gray", annotation_text="Neutral")
        st.plotly_chart(fig, use_container_width=True)


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: LOG EN VIVO
# ══════════════════════════════════════════════════════════════════════════════
elif page == "📋 Log en vivo":
    st.title("📋 Log del Research en vivo")

    if not RESEARCH_LOG.exists():
        st.warning("No hay log de research activo.")
        st.stop()

    lines = RESEARCH_LOG.read_text(errors="replace").splitlines()
    total_lines = len(lines)

    # Progress from log
    progress_lines = [l for l in lines if "/" in l and "experiment" in l.lower()]
    bar_lines = [l for l in lines if l.strip().startswith("[") and "/6552]" in l]

    if bar_lines:
        last = bar_lines[-1]
        try:
            done = int(last.split("[")[1].split("/")[0].strip())
            pct = done / 6552
            st.progress(min(pct, 1.0))
            st.metric("Progreso", f"{done:,} / 6,552 experimentos ({pct*100:.1f}%)")
        except Exception:
            pass

    st.markdown("---")
    st.subheader(f"Últimas 100 líneas del log ({total_lines} total)")

    last_lines = lines[-100:]
    log_text = "\n".join(last_lines)
    st.code(log_text, language=None)

    st.caption(f"Archivo: {RESEARCH_LOG} | {total_lines} líneas | Última modificación: "
               f"{datetime.fromtimestamp(RESEARCH_LOG.stat().st_mtime).strftime('%H:%M:%S')}")
