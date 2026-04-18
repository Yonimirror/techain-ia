"""
Techain-IA — Dashboard de Posiciones
Lanzar con: streamlit run apps/dashboard/positions.py
"""
from __future__ import annotations
import json
from datetime import datetime
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

st.set_page_config(page_title="Techain-IA · Posiciones", layout="wide", initial_sidebar_state="collapsed")

PAPER_STATE_DIR = Path("data/paper_state")

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.title("Config")
    refresh = st.selectbox("Auto-refresh", [0, 30, 60, 120], index=2)
    if refresh:
        st.markdown(f'<meta http-equiv="refresh" content="{refresh}">', unsafe_allow_html=True)

# ── Helpers ───────────────────────────────────────────────────────────────────

def _to_float(val) -> float:
    try:
        return float(str(val).replace("$","").replace("%","").replace("+","").replace(",","").strip())
    except Exception:
        return 0.0

def color_val(val) -> str:
    v = _to_float(val)
    if v > 0:   return "color:#00c853;font-weight:bold"
    elif v < 0: return "color:#d32f2f;font-weight:bold"
    return ""

def color_wr(val) -> str:
    v = _to_float(val)
    if v >= 70: return "color:#00c853;font-weight:bold"
    if v < 50:  return "color:#ff6d00"
    return ""

def color_result(val) -> str:
    s = str(val)
    if "WIN" in s:  return "color:#00c853;font-weight:bold"
    if "LOSS" in s: return "color:#d32f2f;font-weight:bold"
    return ""

@st.cache_data(ttl=30)
def load_states() -> list[dict]:
    states = []
    for f in sorted(PAPER_STATE_DIR.glob("*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            d["_file"] = f.stem
            states.append(d)
        except Exception:
            pass
    return states

def compute_pnls(closed_trades: list) -> list[float]:
    pnls = []
    for t in closed_trades:
        ep  = float(t["entry_price"])
        xp  = float(t.get("exit_price") or ep)
        qty = float(t["quantity"])
        fees= float(t.get("fees") or 0)
        side= t.get("side", "BUY")
        pnl = (xp - ep) * qty - fees if side == "BUY" else (ep - xp) * qty - fees
        pnls.append(pnl)
    return pnls

def describe_strategy(sid: str) -> dict:
    """
    Devuelve descripción en lenguaje llano de cada estrategia.
    """
    # Extraer partes del session_id
    family = "smart_money" if sid.startswith("smart_money") else "reversion"
    parts  = sid.split("_")

    # Leer parámetros del nombre
    rsi_period = next((p.replace("rsi","") for p in parts if p.startswith("rsi") and p[3:].isdigit()), "?")
    os_val     = next((p.replace("os","")  for p in parts if p.startswith("os")  and p[2:].isdigit()), "?")
    ob_val     = next((p.replace("ob","")  for p in parts if p.startswith("ob")  and p[2:].isdigit()), "?")
    sl_val     = next((p.replace("sl","")  for p in parts if p.startswith("sl")  and p[2:].isdigit()), "?")
    has_ema    = "ema200" in sid
    symbol     = parts[-2] if len(parts) >= 2 else "?"
    timeframe  = parts[-1] if len(parts) >= 1 else "?"

    tf_name = {"1d": "diario (1 vela/día)", "4h": "4 horas (6 velas/día)"}.get(timeframe, timeframe)
    asset   = {
        "BTC": "Bitcoin", "ETH": "Ethereum", "SOL": "Solana",
        "NVDA": "NVIDIA", "MSFT": "Microsoft", "AAPL": "Apple",
        "AMZN": "Amazon", "TSLA": "Tesla", "AMD": "AMD",
        "META": "Meta", "GOOGL": "Google", "NFLX": "Netflix",
        "COIN": "Coinbase",
        "CL=F": "Petróleo crudo (WTI)", "GC=F": "Oro futuros",
        "ES=F": "S&P 500 futuros", "NQ=F": "Nasdaq futuros",
        "SPY": "S&P 500 ETF", "QQQ": "Nasdaq ETF", "GLD": "Oro ETF",
    }.get(symbol, symbol)

    logic = (
        f"Compra {asset} cuando el RSI-{rsi_period} cae por debajo de {os_val} "
        f"(mercado sobrevendido, se espera rebote). "
        f"Vende cuando el RSI supera {ob_val} (mercado sobrecomprado) "
        f"o si la pérdida supera el {sl_val}% (stop-loss)."
    )
    if has_ema:
        logic += f" Solo opera si el precio está por encima de la media móvil de 200 días (EMA200) — filtro de tendencia alcista."
    if family == "smart_money":
        logic += " Además filtra con Smart Money: si las ballenas (grandes inversores) están vendiendo a los exchanges, bloquea la entrada aunque el RSI esté sobrevendido."

    timeframe_note = (
        "Genera señales una vez al día (cierre de vela diaria). "
        "Cada trade puede durar días o semanas." if timeframe == "1d"
        else "Genera señales cada 4 horas. Trades más frecuentes pero con más ruido."
    )

    return {
        "asset": asset,
        "symbol": symbol,
        "timeframe": tf_name,
        "rsi_period": rsi_period,
        "oversold": os_val,
        "overbought": ob_val,
        "stop_loss": sl_val,
        "has_ema": has_ema,
        "family": family,
        "logic": logic,
        "timeframe_note": timeframe_note,
    }

# ══════════════════════════════════════════════════════════════════════════════
# LOAD DATA
# ══════════════════════════════════════════════════════════════════════════════
states = load_states()
if not states:
    st.warning("No hay estados de paper trading. Ejecuta el trader primero.")
    st.stop()

rows = []
for s in states:
    sid     = s["session_id"]
    cash    = float(s["cash"])
    initial = float(s["initial_capital"])
    peak    = float(s.get("peak_equity") or initial)
    ret_pct = (cash - initial) / initial * 100
    dd_pct  = (peak - cash) / peak * 100 if peak > 0 else 0.0
    closed  = s.get("closed_trades", [])
    open_t  = s.get("open_trades", [])
    pnls    = compute_pnls(closed)
    n       = len(pnls)
    wins    = sum(1 for p in pnls if p > 0)
    wr      = wins / n * 100 if n else 0
    rs      = s.get("risk_state", {})
    ks      = rs.get("kill_switch_active", False)
    consec  = rs.get("consecutive_losses", 0)
    desc    = describe_strategy(sid)
    last_bar= s.get("last_bar_timestamp", "")[:10]

    rows.append({
        "_sid": sid, "_ks": ks, "_pnls": pnls, "_equity": cash,
        "_initial": initial, "_open": open_t, "_desc": desc,
        "_state": s,
        "KS":       "🔴 KS" if ks else "🟢",
        "Estrategia": "_".join(sid.split("_")[:-2]),
        "Activo":   f"{desc['symbol']} {desc['timeframe'].split()[0]}",
        "Trades":   n,
        "WR":       wr,
        "PnL ($)":  sum(pnls),
        "Retorno":  ret_pct,
        "Max DD":   dd_pct,
        "Abierta":  "🟡 Sí" if open_t else "—",
        "Pérd.consec": consec,
        "Última barra": last_bar,
    })

df = pd.DataFrame(rows)

# ══════════════════════════════════════════════════════════════════════════════
# CABECERA
# ══════════════════════════════════════════════════════════════════════════════
st.markdown("## Techain-IA · Monitor de estrategias")
st.caption(
    f"Actualizado: {datetime.now().strftime('%d/%m/%Y %H:%M')}  |  "
    f"{len(states)} estrategias activas  |  {int(df['Trades'].sum())} trades cerrados  |  "
    f"Datos en tiempo real de Binance · velas diarias"
)

# ── Explicación general ────────────────────────────────────────────────────────
with st.expander("¿Qué estás viendo aquí? — Haz clic para leer", expanded=False):
    st.markdown("""
**Techain-IA es un agente de paper trading** — opera con dinero simulado ($1.000.000 por estrategia)
siguiendo reglas matemáticas basadas en indicadores técnicos.

**¿Por qué tantas estrategias a la vez?**
Estamos en **fase de validación**: probamos variantes similares simultáneamente para identificar
cuáles tienen un comportamiento consistente con el backtest histórico.
Las que demuestren +50 trades con Win Rate ≥ 70% pasarán a trading real con capital pequeño ($5.000).

**¿Qué significan los colores?**
- 🟢 Verde = estrategia funcionando, sin alarmas
- 🔴 KS = Kill Switch activado — la estrategia dejó de operar porque superó el límite de pérdida diaria
- Win Rate verde ≥ 70% | naranja 50-69% | rojo < 50%

**Horizon temporal esperado:**
- Velas diarias (1d): 1 trade cada 2-4 semanas en condiciones normales de mercado
- Velas 4h: 1-3 trades por semana
- Validación completa estimada: **4-8 semanas desde el inicio** (estamos en la semana 1)
    """)

st.markdown("---")

# ── KPIs globales ─────────────────────────────────────────────────────────────
total_equity  = df["_equity"].sum()
total_initial = df["_initial"].sum()
total_pnl     = df["PnL ($)"].sum()
total_trades  = int(df["Trades"].sum())
wins_all      = sum(sum(1 for p in r["_pnls"] if p > 0) for _, r in df.iterrows())
global_wr     = wins_all / total_trades * 100 if total_trades else 0
active_ks     = int(df["_ks"].sum())
open_pos      = int(df["_open"].apply(lambda x: 1 if x else 0).sum())
ret_global    = (total_equity - total_initial) / total_initial * 100

c1,c2,c3,c4,c5,c6 = st.columns(6)
c1.metric("Capital total (simulado)", f"${total_equity:,.0f}", f"{ret_global:+.2f}%")
c2.metric("Ganancia total simulada",  f"${total_pnl:+,.0f}")
c3.metric("Trades cerrados",          total_trades)
c4.metric("Win Rate global",          f"{global_wr:.1f}%",
          delta="objetivo ≥70%" if global_wr < 70 else "objetivo alcanzado ✓")
c5.metric("Posiciones abiertas ahora",open_pos)
c6.metric("Kill Switches activos",    active_ks,
          delta="⚠️ hay alarmas" if active_ks else "todo OK",
          delta_color="inverse" if active_ks else "off")

st.markdown("---")

# ══════════════════════════════════════════════════════════════════════════════
# TABLA PRINCIPAL
# ══════════════════════════════════════════════════════════════════════════════
st.subheader("Estado por estrategia")
st.caption(
    "Cada fila es una variante de la estrategia RSI Mean Reversion probándose en paralelo. "
    "Los números son **simulados** — no hay dinero real en juego todavía."
)

disp = df[[
    "KS","Estrategia","Activo","Trades","WR","PnL ($)","Retorno","Max DD","Abierta","Pérd.consec","Última barra"
]].copy()

disp["WR"]      = disp["WR"].apply(lambda x: f"{x:.0f}%")
disp["PnL ($)"] = disp["PnL ($)"].apply(lambda x: f"${x:+,.0f}")
disp["Retorno"] = disp["Retorno"].apply(lambda x: f"{x:+.2f}%")
disp["Max DD"]  = disp["Max DD"].apply(lambda x: f"{x:.2f}%")

styled = (
    disp.style
    .map(color_val,  subset=["Retorno","PnL ($)"])
    .map(color_wr,   subset=["WR"])
)
st.dataframe(styled, use_container_width=True, hide_index=True, height=490)

# ── Leyenda de columnas ────────────────────────────────────────────────────────
with st.expander("¿Qué significa cada columna?"):
    st.markdown("""
| Columna | Significado |
|---|---|
| **KS** | Kill Switch — 🔴 = estrategia pausada por superar límite de pérdida |
| **Estrategia** | Nombre técnico: rsi7 = RSI de 7 días, os35 = sobrevendido en 35, ob60 = sobrecomprado en 60, sl5 = stop loss 5%, ema200 = filtro de tendencia |
| **Activo** | Criptomoneda y timeframe (1d = diario, 4h = cada 4 horas) |
| **Trades** | Número de operaciones completadas (entrada + salida) |
| **WR** | Win Rate — porcentaje de trades ganadores. Objetivo: ≥ 70% |
| **PnL ($)** | Ganancia/pérdida total acumulada en dólares simulados |
| **Retorno** | Retorno % sobre el capital inicial de esa estrategia |
| **Max DD** | Máxima caída desde el pico de equity — mide el riesgo real vivido |
| **Abierta** | Si hay una posición abierta en este momento |
| **Pérd.consec** | Pérdidas consecutivas actuales — si llega a 5 activa Kill Switch |
| **Última barra** | Fecha de la última vela procesada |
    """)

st.markdown("---")

# ══════════════════════════════════════════════════════════════════════════════
# GRÁFICOS
# ══════════════════════════════════════════════════════════════════════════════
col_l, col_r = st.columns(2)

with col_l:
    st.subheader("Retorno % por estrategia")
    sorted_ret = df.sort_values("Retorno", ascending=True)
    colors = ["#d32f2f" if r < 0 else "#00c853" for r in sorted_ret["Retorno"]]
    labels = sorted_ret["Estrategia"].str[:35].tolist()
    fig = go.Figure(go.Bar(
        x=sorted_ret["Retorno"], y=labels, orientation="h",
        marker_color=colors,
        text=sorted_ret["Retorno"].apply(lambda x: f"{x:+.2f}%"),
        textposition="outside",
    ))
    fig.update_layout(
        margin=dict(l=0,r=70,t=5,b=5), height=max(300, len(df)*26),
        xaxis_title="Retorno (%)", yaxis_title="",
        xaxis=dict(zeroline=True, zerolinecolor="#555"),
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)", font=dict(size=10),
    )
    st.plotly_chart(fig, use_container_width=True)

with col_r:
    st.subheader("Win Rate % por estrategia")
    sorted_wr = df.sort_values("WR", ascending=True)
    colors_wr = ["#d32f2f" if w < 50 else "#ff6d00" if w < 70 else "#00c853" for w in sorted_wr["WR"]]
    labels_wr = sorted_wr["Estrategia"].str[:35].tolist()
    fig2 = go.Figure(go.Bar(
        x=sorted_wr["WR"], y=labels_wr, orientation="h",
        marker_color=colors_wr,
        text=sorted_wr["WR"].apply(lambda x: f"{x:.0f}%"),
        textposition="outside",
    ))
    fig2.add_vline(x=70, line_dash="dot", line_color="#00c853", annotation_text="Objetivo 70%")
    fig2.add_vline(x=50, line_dash="dot", line_color="#555",    annotation_text="Mínimo 50%")
    fig2.update_layout(
        margin=dict(l=0,r=60,t=5,b=5), height=max(300, len(df)*26),
        xaxis_title="Win Rate (%)", xaxis=dict(range=[0,110]),
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)", font=dict(size=10),
    )
    st.plotly_chart(fig2, use_container_width=True)

# ── PnL acumulado global ───────────────────────────────────────────────────────
st.subheader("PnL acumulado — todos los trades de todas las estrategias")
st.caption("Cada punto = un trade cerrado. La curva sube con ganancias, baja con pérdidas.")

all_pnls = []
for _, row in df.iterrows():
    all_pnls.extend(row["_pnls"])

if all_pnls:
    cumsum = [sum(all_pnls[:i+1]) for i in range(len(all_pnls))]
    color_line = "#00c853" if cumsum[-1] >= 0 else "#d32f2f"
    fig3 = go.Figure()
    fig3.add_trace(go.Scatter(
        y=cumsum, mode="lines",
        line=dict(color=color_line, width=2),
        fill="tozeroy", fillcolor=f"rgba({'0,200,83' if cumsum[-1]>=0 else '211,47,47'},0.12)",
    ))
    fig3.add_hline(y=0, line_color="#555", line_width=1)
    fig3.update_layout(
        margin=dict(l=0,r=0,t=5,b=5), height=220,
        xaxis_title="Nº trade", yaxis_title="PnL acumulado ($)",
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)", showlegend=False,
    )
    st.plotly_chart(fig3, use_container_width=True)

st.markdown("---")

# ══════════════════════════════════════════════════════════════════════════════
# DETALLE POR ESTRATEGIA
# ══════════════════════════════════════════════════════════════════════════════
st.subheader("Detalle de una estrategia")

options = df["Estrategia"].tolist()
selected_label = st.selectbox("Selecciona estrategia", options, label_visibility="collapsed")
sel = df[df["Estrategia"] == selected_label].iloc[0]
desc = sel["_desc"]
s_state = sel["_state"]
closed  = s_state.get("closed_trades", [])
pnls    = sel["_pnls"]
n = len(pnls)
wins = sum(1 for p in pnls if p > 0)

# ── Explicación de la estrategia ──────────────────────────────────────────────
ks_warn = "⚠️ **KILL SWITCH ACTIVO** — Esta estrategia está pausada porque superó el límite de pérdida diaria. Se reactivará automáticamente mañana si la pérdida fue por límite diario, o requiere revisión manual si fue por pérdidas consecutivas." if sel["_ks"] else ""

st.info(f"""
**¿Qué hace esta estrategia?**

{desc['logic']}

**Timeframe:** {desc['timeframe']}. {desc['timeframe_note']}

**Estado actual:** {'🟢 Operativa' if not sel['_ks'] else '🔴 Pausada (Kill Switch)'}
**Última vela procesada:** {sel['Última barra']}
**Posición abierta ahora:** {'Sí — hay una compra en curso' if sel['_open'] else 'No — esperando señal'}
""" + (f"\n\n{ks_warn}" if ks_warn else ""))

# ── Métricas de la estrategia ─────────────────────────────────────────────────
cash    = sel["_equity"]
initial = sel["_initial"]
ret_pct = (cash - initial) / initial * 100
wr = wins/n*100 if n else 0

m1,m2,m3,m4,m5 = st.columns(5)
m1.metric("Trades cerrados", n,        delta=f"objetivo: 50" if n < 50 else "✓ validado")
m2.metric("Win Rate",        f"{wr:.0f}%", delta="≥70% objetivo" if wr >= 70 else f"{70-wr:.0f}pp para objetivo")
m3.metric("PnL simulado",    f"${sum(pnls):+,.0f}")
m4.metric("Retorno",         f"{ret_pct:+.2f}%")
m5.metric("Pérd. consecutivas", int(sel["Pérd.consec"]),
          delta="OK" if sel["Pérd.consec"] < 3 else "⚠️ atención",
          delta_color="off" if sel["Pérd.consec"] < 3 else "inverse")

# ── Validación progress ────────────────────────────────────────────────────────
progress_to_50 = min(n / 50, 1.0)
st.markdown(f"**Progreso hacia validación** ({n}/50 trades para decisión de ir a real)")
st.progress(progress_to_50)
if n >= 50 and wr >= 70:
    st.success("Esta estrategia ha alcanzado los 50 trades con Win Rate ≥ 70%. Lista para evaluar paso a trading real.")
elif n >= 50:
    st.warning(f"50 trades alcanzados pero Win Rate ({wr:.0f}%) por debajo del objetivo (70%). Seguir monitorizando.")
else:
    weeks_remaining = max(1, (50 - n) // 2)  # ~2 trades/semana en 1d
    st.caption(f"Estimación: {50-n} trades más  ≈  {weeks_remaining}-{weeks_remaining*2} semanas a este ritmo de mercado")

# ── Tabla de trades ────────────────────────────────────────────────────────────
if closed:
    st.markdown("**Últimos 20 trades** (más recientes arriba)")
    trade_rows = []
    for i, t in enumerate(reversed(closed[-20:])):
        ep   = float(t["entry_price"])
        xp   = float(t.get("exit_price") or ep)
        qty  = float(t["quantity"])
        fees = float(t.get("fees") or 0)
        side = t.get("side", "BUY")
        pnl  = (xp - ep) * qty - fees if side == "BUY" else (ep - xp) * qty - fees
        ret_t= pnl / (ep * qty) * 100

        opened   = t.get("opened_at", "")[:16].replace("T", " ")
        closed_at= t.get("closed_at", "")[:16].replace("T", " ")

        trade_rows.append({
            "#":         len(closed) - i,
            "Resultado": "✅ WIN" if pnl > 0 else "❌ LOSS",
            "Entrada":   f"${ep:,.0f}",
            "Salida":    f"${xp:,.0f}",
            "Qty":       f"{qty:.4f}",
            "PnL":       f"${pnl:+,.0f}",
            "Ret.":      f"{ret_t:+.2f}%",
            "Fees":      f"${fees:,.0f}",
            "Fecha apertura":  opened,
            "Fecha cierre":    closed_at,
        })

    df_detail = pd.DataFrame(trade_rows)
    styled_d = (
        df_detail.style
        .map(color_result, subset=["Resultado"])
        .map(color_val,    subset=["PnL","Ret."])
    )
    st.dataframe(styled_d, use_container_width=True, hide_index=True)

    # Mini equity curve
    eq = s_state.get("equity_curve", [])
    if len(eq) > 2:
        eq_vals = [float(p["equity"]) for p in eq]
        color_eq = "#00c853" if eq_vals[-1] >= eq_vals[0] else "#d32f2f"
        fig_eq = go.Figure(go.Scatter(
            y=eq_vals, mode="lines",
            line=dict(color=color_eq, width=1.5),
            fill="tozeroy", fillcolor=f"rgba({'0,200,83' if eq_vals[-1]>=eq_vals[0] else '211,47,47'},0.1)",
        ))
        fig_eq.add_hline(y=eq_vals[0], line_dash="dot", line_color="#555", annotation_text="Capital inicial")
        fig_eq.update_layout(
            margin=dict(l=0,r=0,t=5,b=5), height=150,
            xaxis=dict(visible=False), yaxis_title="Equity ($)",
            plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)", showlegend=False,
        )
        st.plotly_chart(fig_eq, use_container_width=True)
else:
    st.info("Sin trades cerrados para esta estrategia todavía. Esperando primera señal.")

# ── Glosario ──────────────────────────────────────────────────────────────────
st.markdown("---")
with st.expander("📖 Glosario — ¿Qué significa cada término? (para principiantes)", expanded=False):
    st.markdown("""
### Conceptos básicos

**Trade / Operación**
Una compra seguida de una venta. El sistema compra un activo, espera que suba, y lo vende. Eso es un trade completo.

**Win Rate (WR) — Tasa de acierto**
De cada 100 operaciones, cuántas terminan en ganancia. Un WR del 70% significa que 7 de cada 10 trades ganan dinero. Por encima del 60% se considera bueno.

**Retorno**
Cuánto ha ganado o perdido la estrategia en total, expresado en porcentaje. Un retorno de +10% sobre $1.000.000 significa $100.000 de ganancia simulada.

**Stop Loss (SL)**
Límite de pérdida por operación. Si el sistema compra y el precio cae un 5%, vende automáticamente para no perder más. Es el seguro de cada trade.

**RSI (Relative Strength Index)**
Indicador que mide si un activo está "muy vendido" (pánico) o "muy comprado" (euforia). Va de 0 a 100. El sistema compra cuando baja de 25-35 (pánico) y vende cuando sube a 60-70 (recuperación).

**Mean Reversion (Reversión a la media)**
La idea de que cuando un precio cae mucho, tiende a volver a su nivel normal. El sistema apuesta a ese rebote.

**EMA200 (Media Móvil de 200 días)**
Promedio del precio de los últimos 200 días. Si el precio está por encima, el mercado está en tendencia alcista. Cuando el sistema usa este filtro, solo compra si el precio está por encima — evita comprar en caídas largas.

**Sharpe Ratio**
Mide cuánto gana la estrategia por cada unidad de riesgo que asume. Por encima de 1 es aceptable, por encima de 2 es bueno, por encima de 3 es muy bueno. Cuanto más alto, mejor.

**Profit Factor (PF)**
Divide las ganancias totales entre las pérdidas totales. Un PF de 2.0 significa que por cada €1 que pierde, gana €2. Por encima de 1.5 es bueno.

**Drawdown**
La caída máxima desde el punto más alto de capital. Si el sistema llegó a $1.100.000 y luego bajó a $1.000.000, el drawdown es del 9%. Mide el peor momento que ha vivido la estrategia.

**Kill Switch**
Interruptor de emergencia. Si las pérdidas superan un límite en un día, el sistema para automáticamente todas las operaciones hasta el día siguiente. Evita catástrofes.

**Paper Trading**
Trading simulado con dinero ficticio. El sistema opera exactamente igual que si fuera real, pero sin arriesgar dinero verdadero. Sirve para validar que la estrategia funciona antes de invertir capital real.

**Walk-Forward**
Técnica de validación que divide el histórico en períodos y comprueba si la estrategia funciona en datos que no ha "visto" antes. Evita el overfitting (que la estrategia solo funcione en el pasado).

**Overfitting**
Cuando una estrategia está tan ajustada al pasado que no funciona en el futuro. Es el error más común en trading algorítmico.

**Timeframe (Marco temporal)**
El tamaño de cada vela en el gráfico. **1d** = una vela por día. **4h** = una vela cada 4 horas. Timeframes menores = más señales pero más ruido.

**Edge**
Ventaja estadística demostrada. Tener "edge" significa que tu sistema gana más de lo que pierde de forma consistente y no aleatoria.
    """)

# ── Pie de página ──────────────────────────────────────────────────────────────
st.markdown("---")
st.caption(
    "Techain-IA · Paper Trading · Todo el capital es simulado · "
    "Las estrategias no operan dinero real hasta superar validación (50 trades, WR ≥ 70%)"
)
