# Sistema de Tiers — Asignación de capital por estrategia
# Calibrado con datos de paper trading reales (2026-04-18)

---

## Arquitectura de 3 Tiers

### TIER 1 — Capital alto: 8-10% por trade
**Criterio de entrada (TODOS deben cumplirse):**
- WF Sharpe > 6.0
- PF en paper > 3.0
- Mínimo 10 trades confirmados en paper
- WR en paper > 60%

**Estrategias clasificadas como Tier 1:**

| Activo | Estrategia              | Trades | WR    | PF paper | Asignación |
|--------|-------------------------|--------|-------|----------|------------|
| NVDA   | rsi7_os35_ob65_ema200   | 12     | 92%   | 57.83    | 10%        |
| NVDA   | rsi7_os35_ob60_ema200   | 12     | 92%   | 46.87    | 10%        |
| AVGO   | rsi7_os30_ob60_sl3      | 11     | 64%   | 5.67     | 8%         |
| AVGO   | rsi7_os35_ob60_sl5      | 13     | 69%   | 3.78     | 8%         |

**Notas:**
- NVDA ema200 con WR 92% y PF >46 son las únicas que justifican
  el máximo de 10%. El edge es tan extremo que infraponderar sería
  desperdiciar la mejor señal del sistema.
- AVGO os30_sl3 con PF 5.67 entra limpia. AVGO os35_sl5 con PF 3.78
  entra justa — si PF baja de 3.0 en próximos 10 trades, bajar a Tier 2.

---

### TIER 2 — Capital estándar: 5% por trade
**Criterio de entrada:**
- WF Sharpe > 4.0
- PF en paper > 1.5
- Mínimo 6 trades confirmados en paper
- No en lista de probación

**Estrategias clasificadas como Tier 2 (14 estrategias):**

| Activo | Estrategia                           | WF Sharpe | Notas                          |
|--------|--------------------------------------|-----------|--------------------------------|
| XLE    | bollinger_bb15_std20_rsi14_os35_ob65 | 10.61     | ETF energía, sin paper aún     |
| FCX    | rsi14_os35_ob65_sl5                  | 9.84      | Mejor FCX                      |
| FCX    | rsi14_os35_ob60_sl3                  | 9.02      | Sólida                         |
| FCX    | rsi14_os35_ob60_sl5                  | 9.02      | Sólida                         |
| XLE    | bollinger_bb25_std15_rsi14_os35_ob65 | 8.55      | 2º rep XLE familia diferente   |
| NVDA   | rsi7_os35_ob60_sl5                   | 6.97      | Sin EMA, más trades            |
| SMH    | bollinger_bb20_std15_rsi14_os40_ob65 | 6.79      | 27 trades, alta frecuencia     |
| BTC 4h | rsi14_os25_ob70_sl5                  | 5.95      | Mejor BTC del portfolio        |
| CL=F   | rsi7_os30_ob60_sl3                   | 5.02      | Futuros petróleo               |
| SMH    | reversion_rsi7_os35_ob70_sl5_ema200  | 5.00      | ETF semis, familia reversion   |
| GLD    | bollinger_bb20_std15_rsi7_os40_ob65  | 4.88      | Oro, sin paper aún             |
| MSFT   | rsi7_os30_ob60_sl5                   | 4.71      | Estable                        |
| BTC 4h | rsi14_os25_ob60_sl5                  | 4.35      | Segunda mejor BTC              |
| BTC 4h | rsi14_os25_ob65_sl5                  | 2.89      | Tercera BTC, diversifica salida|

**Notas:**
- ETFs nuevos (XLE, SMH, GLD) entran en Tier 2 automáticamente
  por WF alto, pero no pueden subir a Tier 1 hasta acumular 10 trades
  de paper.
- FCX tiene WR 43-50% lo cual es normal para commodities — el edge
  viene de AvgWin >> AvgLoss, no de acertar mucho.

---

### TIER 3 — Capital mínimo: 2-3% por trade
**Criterio de entrada:**
- WF Sharpe > 1.5
- PF en paper > 1.0 (no perdiendo dinero)
- En observación, recién activadas, o con métricas borderline

**Estrategias clasificadas como Tier 3 (12 estrategias):**

| Activo | Estrategia                          | WF Sharpe | Notas                          |
|--------|-------------------------------------|-----------|--------------------------------|
| ETH 4h | rsi14_os30_ob60_sl5                 | 5.25      | Borderline PF paper 1.18       |
| AVGO   | rsi7_os35_ob60_sl3_ema200           | 5.10      | Probación 30d, WR paper 43%    |
| NVDA   | bollinger_bb25_std20_rsi7_os35      | 4.09      | Recién activado, sin paper     |
| NVDA   | bollinger_bb25_std20_rsi14_os40     | 4.09      | Recién activado, sin paper     |
| SOL    | rsi21_os30_ob70_sl3                 | 2.95      | WR 27%, probación 60d          |
| BTC 1d | rsi7_os30_ob60_sl3_ema200           | 2.83      | BTC 1d débil                   |
| SPY    | bollinger_bb20_std15_rsi14_os40_ob60| 2.50      | 1 representante (eran 3)       |
| BTC 1d | rsi7_os30_ob60_sl5_ema200           | 2.11      | BTC 1d débil                   |
| FCX    | rsi7_os30_ob70_sl5                  | 1.90      | Probación 60d                  |
| SOL    | rsi21_os30_ob60_sl3                 | 1.84      | WR 36%, probación              |
| BTC 1d | rsi7_os35_ob60_sl5_ema200           | 1.80      | Candidato próxima limpieza     |
| SOL    | rsi21_os30_ob65_sl3                 | 1.50      | WR 36%, probación              |

---

## Reglas de promoción y descenso

### Subir de Tier
```
Tier 3 → Tier 2:
  - Acumular 10+ trades en paper
  - PF paper > 1.5
  - WR paper > 35%

Tier 2 → Tier 1:
  - Acumular 15+ trades en paper
  - PF paper > 3.0
  - WR paper > 60% (o WR >40% con AvgWin/AvgLoss > 3.0)
  - WF Sharpe > 6.0
```

### Bajar de Tier
```
Tier 1 → Tier 2:
  - PF paper cae por debajo de 2.5 en ventana de 15 trades
  - O WR cae por debajo de 50%

Tier 2 → Tier 3:
  - PF paper cae por debajo de 1.2 en ventana de 15 trades
  - O WR cae por debajo de 25%

Tier 3 → Desactivar:
  - PF paper < 1.0 (perdiendo dinero) en 10+ trades
  - O WR < 20% en 10+ trades
  - O no mejora en período de probación asignado
```

### Frecuencia de revisión
- **Revisión de tiers**: mensual (1er lunes de cada mes)
- **Revisión de probación**: según fecha asignada (30d o 60d)
- **Revisión de emergencia**: si kill switch se activa o drawdown
  portfolio > 3% en una semana

---

## Caps de exposición — protección anti-correlación

### Cap por activo
- Máximo **20%** del capital total expuesto a un mismo activo
  simultáneamente (todas las estrategias del activo combinadas).
- Ejemplo: NVDA tiene 4 estrategias Tier 1+2. Si todas se activan
  a la vez: 10% + 10% + 5% + 5% = 30% → el cap limita a 20%.
  Se priorizan las de mayor PF.

### Cap por sector
- Máximo **35%** del capital total expuesto a un mismo sector.
- Sectores definidos:
  - Semis: NVDA + AVGO + SMH
  - Energía: CL=F + XLE
  - Metales: FCX + GLD
  - Crypto: BTC + ETH + SOL
  - Macro: MSFT + SPY

### Cap total de exposición
- Máximo **60%** del capital total invertido simultáneamente
  (todas las posiciones abiertas combinadas).
- Reserva mínima de 40% en cash para:
  - Nuevas señales que aparezcan cuando ya hay posiciones abiertas
  - Protección contra gap adverso overnight/weekend
  - Margen para no forzar salidas por falta de liquidez

---

## Ejemplo práctico: selloff de semis

Escenario: NVDA cae 15%, AVGO cae 12%, SMH cae 10% en 3 días.
RSI oversold se activa en todas las estrategias de semis.

**Sin caps (sistema actual, 5% fijo para todo):**
- NVDA ×4 estrategias = 20%
- AVGO ×3 estrategias = 15%
- SMH ×2 estrategias = 10%
- Total semis: 45% del capital en una sola apuesta direccional
- Si el selloff continúa → drawdown catastrófico

**Con tiers + caps:**
1. Sistema intenta abrir NVDA ema200 (Tier 1, 10%) → aprobada
2. Intenta NVDA ob65_ema200 (Tier 1, 10%) → 20% NVDA, cap activo alcanzado
3. Intenta NVDA sin ema (Tier 2, 5%) → rechazada por cap activo
4. Intenta AVGO os30_sl3 (Tier 1, 8%) → 28% semis, aprobada
5. Intenta AVGO os35_sl5 (Tier 1, 8%) → 36% semis, cap sector alcanzado
6. Intenta SMH Bollinger (Tier 2, 5%) → rechazada por cap sector
- Total semis: 28% (no 45%)
- Máximo 3 posiciones de las 9 posibles
- Capital restante disponible para FCX/GLD/BTC si generan señal

---

## Implementación en el Risk Engine

Cambios necesarios en el risk engine actual:

```python
# Estructura de config para tiers
TIER_CONFIG = {
    1: {"allocation_pct": 0.08, "max_allocation_pct": 0.10},  # 8-10%
    2: {"allocation_pct": 0.05},                                # 5%
    3: {"allocation_pct": 0.025},                               # 2.5%
}

# Cada estrategia en experiments.db necesita un campo nuevo: tier (1/2/3)
# Asignación se lee de TIER_CONFIG[strategy.tier]

# Caps (ya se aplican en el risk engine, solo ajustar valores)
MAX_EXPOSURE_PER_ASSET = 0.20     # 20% por activo
MAX_EXPOSURE_PER_SECTOR = 0.35    # 35% por sector
MAX_TOTAL_EXPOSURE = 0.60         # 60% total

# Mapeo sector (nuevo)
SECTOR_MAP = {
    "NVDA": "semis", "AVGO": "semis", "SMH": "semis",
    "FCX": "metals", "GLD": "metals",
    "CL=F": "energy", "XLE": "energy",
    "BTC": "crypto", "ETH": "crypto", "SOL": "crypto",
    "MSFT": "macro", "SPY": "macro",
}

# Prioridad dentro de un sector cuando hay cap:
# Tier 1 antes que Tier 2 antes que Tier 3
# Dentro del mismo Tier: mayor PF primero
```

---

## Impacto esperado vs sistema actual (5% fijo)

| Métrica                    | 5% fijo | Con tiers |
|----------------------------|---------|-----------|
| Capital en mejor señal     | 5%      | 10%       |
| Capital en peor señal      | 5%      | 2.5%      |
| Max exposición semis       | 45%+    | 35%       |
| Max exposición total       | sin cap | 60%       |
| Retorno esperado por trade | igual   | +30-40%*  |
| Max drawdown esperado      | alto    | -40%*     |

*Estimaciones basadas en redistribución de capital de estrategias
 débiles (SOL, ETH débil) a estrategias fuertes (NVDA ema200).
 El retorno sube porque pones más capital donde el edge es mayor.
 El drawdown baja porque limitas exposición sectorial.

---

## Calendario de implementación sugerido

**Semana 1**: Añadir campo `tier` a experiments.db. Asignar tiers
según tabla de arriba. No cambiar sizing aún.

**Semana 2**: Implementar caps de sector y activo en risk engine.
Esto es lo más importante — protege contra correlación.

**Semana 3**: Activar sizing diferencial (8-10% / 5% / 2.5%).
Monitorizar que el sistema prioriza correctamente cuando hay cap.

**Semana 4**: Primera revisión de tiers con datos post-cambio.
Ajustar si algún cap se activa demasiado frecuentemente (>50%
de las señales rechazadas → cap demasiado restrictivo).
