# 🧠 PROTOCOLO DE DESARROLLO – SISTEMA AUTÓNOMO DE INVESTIGACIÓN Y TRADING

## 🎯 OBJETIVO

Construir un sistema semi-autónomo que:
1. Investiga edges estadísticos en múltiples mercados sin intervención humana
2. Valida rigurosamente antes de activar cualquier estrategia
3. Ejecuta con disciplina y gestión de riesgo estricta
4. Aprende de los resultados reales para mejorar la investigación

---

## ⚠️ PRINCIPIO FUNDAMENTAL

> El sistema investiga. El humano supervisa y decide. Nunca al revés.

---

## 🏗️ ARQUITECTURA EN TRES CAPAS

```
CAPA 1 — RESEARCH ENGINE (autónomo)
    Descarga datos → genera hipótesis → testea → filtra → informa

CAPA 2 — STRATEGY REGISTRY (memoria)
    Almacena resultados → puntúa estrategias → detecta degradación

CAPA 3 — EXECUTION ENGINE (disciplinado)
    Recibe estrategia activa → ejecuta → registra resultados reales
                                              ↓
                                    feedback → Research Engine
```

---

## 🔁 FASES DEL SISTEMA

---

### FASE 0 — COMPRENSIÓN DEL MERCADO (humano, continua)

No automatizable. El humano debe entender por qué se mueve el precio.
Corre en paralelo con todo lo demás. No bloquea.

Entregable: `MARKET_CONTEXT.md`

---

### FASE 1 — RESEARCH ENGINE MÍNIMO (sistema)

**El sistema hace esto solo:**

- Descarga datos históricos de: BTC, ETH, SPY (diario + 4h)
- Testea hipótesis de tres familias: tendencia, reversión, momentum
- Corre backtests en paralelo con parámetros variables
- Aplica walk-forward obligatorio a todo
- Aplica filtro de robustez: si el edge desaparece con ±10% en parámetros → descartado
- Incluye fees reales y ejecución en siguiente vela desde el día 1
- Guarda resultados estructurados en base de datos local

**Métricas mínimas obligatorias por estrategia:**
```
sharpe, max_drawdown, profit_factor,
win_rate, expectancy, num_trades,
robustez_parametros, consistencia_periodos
```

**Activos iniciales:** BTC/USDT, ETH/USDT, SPY
**Timeframes iniciales:** 1d, 4h
**Expandible sin cambiar arquitectura**

Entregable: informe semanal legible con top 3-5 estrategias

---

### FASE 2 — STRATEGY REGISTRY (sistema)

- Base de datos local (SQLite) con todas las estrategias validadas
- Score compuesto por: Sharpe + drawdown + robustez + consistencia
- Estado por estrategia: candidata / paper_trading / activa / degradada
- Alerta automática si una estrategia activa se degrada

Entregable: `get_best_strategy()` → retorna la mejor estrategia disponible

---

### FASE 3 — DECISIÓN HUMANA

El humano recibe el informe del Research Engine y decide:
- Qué estrategia activar para paper trading
- Cuándo pasar de paper a capital real
- Cuándo detener el sistema

**El sistema nunca toma estas decisiones solo.**

---

### FASE 4 — PAPER TRADING (sistema, mínimo 3 meses)

- Binance Testnet para crypto / Alpaca Paper para acciones
- Ejecución automática con la estrategia elegida
- Logs completos y auditables
- Métricas reales vs métricas del backtest
- Resultados retroalimentan el Research Engine

Condición de avance: rendimiento real consistente con backtest

---

### FASE 5 — CAPITAL REAL (humano decide)

- Capital inicial: €100
- Stop absoluto: si cae a €80 → parar y revisar
- Reinversión total de ganancias
- Revisión mensual de métricas

---

## 🔄 FEEDBACK LOOP (crítico)

```
Execution genera métricas reales
         ↓
Registry actualiza score de la estrategia
         ↓
Research Engine prioriza hipótesis similares a las que funcionan
Research Engine descarta familias que fallan consistentemente
```

Sin este loop el sistema no evoluciona. Es obligatorio desde el día 1.

---

## 🛡️ FILTROS ANTIOVERFITTING (obligatorios)

Todo resultado debe pasar estos tres filtros antes de entrar al registry:

1. **Walk-forward** — funciona en datos no vistos
2. **Robustez de parámetros** — funciona con ±10% en cada parámetro
3. **Consistencia temporal** — funciona en al menos 3 de cada 4 periodos

Si falla cualquiera → descartado automáticamente

---

## 🚫 ANTI-PATRONES (prohibidos siempre)

- Activar estrategia sin pasar los tres filtros
- Optimizar parámetros sobre datos de test
- Añadir complejidad sin justificación en resultados
- Pasar a capital real sin 3 meses de paper trading positivo
- Construir features sin que el Research Engine esté funcionando

---

## 🏁 DEFINICIÓN DE ÉXITO

El sistema es exitoso si:
- Encuentra al menos 1 estrategia con Sharpe > 1.0 en out-of-sample
- El paper trading replica los resultados del backtest
- El sistema mejora sus resultados con el tiempo gracias al feedback loop

NO es éxito:
- Muchos activos analizados
- Arquitectura compleja
- Backtest perfecto sin validación real

---

## 📌 ORDEN DE CONSTRUCCIÓN

```
1. Backtesting engine robusto (fees + ejecución realista + métricas completas)
2. Research Engine mínimo (descarga + hipótesis + paralelo + walk-forward)
3. Filtros de robustez
4. Strategy Registry + informe legible
5. Feedback loop
6. Conexión con Execution Engine existente
7. Paper trading
8. Capital real
```

---

## 🎯 SCOPE INICIAL (expandible)

| Activos | BTC/USDT, ETH/USDT, SPY |
|---------|------------------------|
| Timeframes | 1d, 4h |
| Familias de estrategias | Tendencia, Reversión, Momentum |
| Capital inicial | €100 |
| Stop loss de cuenta | €80 |
