# Patrones nuevos — research batch 2026-04-16

---

## Patrón: Cualidad cíclica del negocio ≠ cualidad MR del precio

**Hipótesis cualitativa que falló:** activos con perfil de negocio cíclico perfecto
(OXY: commodity price, Berkshire floor, volatilidad geopolítica externa, no
deuda estructural) deberían ser candidatos ideales para RSI mean reversion.

**Resultado empírico:** OXY rechazada. Sharpe negativo en casi todas las
variantes testadas.

**Explicación propuesta:** RSI mean reversion exige drawdowns **V-shape**
(castigo rápido + perdón rápido). Los tickers commodity-linked tienen
drawdowns **U-shape prolongados**: cuando WTI entra en régimen bajista, OXY
se queda oversold durante semanas o meses. El RSI oversold marca entrada
demasiado pronto y el precio sigue cayendo hasta que cambia el régimen de
commodity. Es el anti-NVDA: castigo largo, perdón lento.

**Regla de decisión:**
- No asumir que "cíclico" = "mean-reverting" en timeframe diario.
- Antes de aceptar un candidato cíclico, inspeccionar la **forma típica**
  de sus drawdowns históricos: contar días desde mínimo local hasta
  recuperar el 50% del drawdown. Si mediana > 30 días, es candidato U-shape
  y probablemente falle en RSI MR.
- Tickers con exposición directa a un commodity en régimen bajista
  estructural (oil en transición energética, por ejemplo) son sospechosos
  por defecto.

**Casos confirmados:** OXY (rechazo empírico, 2026-04-16)
**Casos a revisar con este criterio:** otros E&P puros (FANG, DVN, EOG),
mineras sin diversificación (VALE).

---

## Patrón: El régimen actual del ticker mata las señales

**Hipótesis cualitativa que falló:** MU (memoria, el subsector más cíclico
de semis) y NEM (oro, mega-cap líquido con ciclo macro limpio) deberían
ser top candidatos MR.

**Resultado empírico:**
- NEM rechazada (Sharpe máximo 1.04).
- MU parcial, pocos trades (no pasa umbral limpio).

**Explicación propuesta:** ambos activos están en régimen alcista vertical
en 2024-2026. El oro rompiendo máximos históricos continuos; MU en
supercycle DRAM sin corrección significativa. **En régimen alcista muy
fuerte, el RSI rara vez llega a oversold profundo** — los pullbacks son
superficiales y el precio recupera antes de generar señal.

El problema no es el ticker, es el momento. Si el oro corrige 25-30% en
algún momento, NEM podría pasar filtros entonces. Misma lógica para MU
cuando el cycle DRAM gire.

**Regla de decisión:**
- Al research de un ticker, comprobar la distribución temporal de las
  señales RSI oversold en los últimos 3 años. Si >70% de las señales están
  concentradas en un subperíodo (ej: 2022 bear market) y <30% en el año
  más reciente, el ticker está en régimen que apaga las señales → rechazar
  o marcar para re-research futuro.
- Mantener un **watchlist de re-research**: tickers rechazados por régimen,
  no por tesis. Re-evaluar cuando: (a) el commodity/sector subyacente
  entre en corrección >20%, o (b) trimestralmente por defecto.

**Casos confirmados:** NEM, MU parcial (2026-04-16)
**Watchlist re-research:** NEM, MU. Re-evaluar si el oro corrige o si
el cycle DRAM entra en downphase.

---

## Meta-patrón: El pipeline de research refuta intuición sectorial

De 6 candidatos propuestos cualitativamente con tesis sólida:
- 3 aprobados (FCX, TSM, AVGO — este último no era de esta batch pero
  confirma la lógica).
- 1 joya confirmada (NVDA).
- 2 rechazos contraintuitivos (OXY, NEM) por los patrones de arriba.
- 2 parciales (MU, AMD).

**Lectura:** la intuición sectorial acierta en ~50% de candidatos. El
pipeline de backtesting con WF + consistencia 100% es el filtro real.
No saltarse research empírico por convicción cualitativa, incluso cuando
la tesis parezca obvia.
