# Techain-IA System Resilience

## Anti-Apagón: Cómo el sistema maneja interrupciones

### 1. **Watchdog Service** ✅
El corazón de la resiliencia. Monitorea cada 5 minutos:

**Archivo:** `apps/watchdog_service.py`

**Lo que hace:**
- Verifica si el **Paper Trader** está corriendo
- Verifica si el **Dashboard** está corriendo  
- Si no están corriendo → **los reinicia automáticamente**
- Si el Trader no ejecutó en 25 horas → lo reinicia (posible apagón)
- Registra TODOS los eventos en `logs/watchdog.log`

**Logs:**
```
[CHECK] Trader=True | Dashboard=True      ← OK
[WARN] Dashboard no esta corriendo         ← Problema detectado
[START] Iniciando dashboard...             ← Acción tomada
[OK] Dashboard iniciado                    ← Éxito
```

---

### 2. **State Persistence** ✅
Aunque el PC se apague durante un trade, **NO se pierde nada**:

**Archivo:** `core/portfolio_engine/persistence.py`

**Qué se guarda:**
- Cash y equity del portfolio
- Trades abiertos y cerrados
- Equity curve (historial)
- Timestamp del último bar procesado
- Estado del risk engine (kill switch, etc)

**Dónde se guarda:**
```
data/paper_state/
├── reversion_rsi7_os35_ob60_sl5_BTC_1d.json       ← Estado 1d
├── reversion_rsi7_os35_ob60_sl5_ema200_BTC_1d.json ← Estado 1d + EMA
└── ... (más estrategias)
```

**Al reiniciar:**
1. Watchdog relanza Trader
2. Trader carga `data/paper_state/*.json`
3. Portfolio se reconstruye con último estado
4. Solo procesa barras NUEVAS (después del último bar guardado)
5. Cero pérdida de datos ✅

---

### 3. **Scheduler de Windows** ✅
Tarea programada para ejecutar Trader diariamente a las 08:00:

**Tarea 1:** `TeChain Paper Trader Daily`
- Ejecuta `run_paper_trader_scheduled.bat` cada día a las 08:00
- Si el PC estaba apagado, ejecuta cuando se encienda

**Tarea 2:** `TeChain Paper Trader Daily` (VBS backup)
- Backup redundante en caso de que falle la Tarea 1

---

## 🔄 Flujo de Recuperación Post-Apagón

```
PC se apaga
    ↓
PC se enciende
    ↓
Windows inicia Watchdog (registrado en Task Scheduler)
    ↓
Watchdog detecta que Trader no está corriendo
    ↓
Watchdog inicia Trader
    ↓
Trader carga estado de data/paper_state/*.json
    ↓
Trader procesa SOLO barras nuevas
    ↓
Portfolio está restaurado al estado anterior ✅
```

---

## 📊 Monitoreo

**Ver salud del sistema:**
```bash
# Logs del watchdog (cada 5 minutos)
tail -50 logs/watchdog.log

# Timestamp del último estado guardado
ls -lh data/paper_state/*.json | head -5

# Procesos corriendo
tasklist | grep -E "python|streamlit"

# Ver si scheduler ejecutó hoy
ls -lh data/paper_state/*.json | grep "$(date +%Y-%m-%d)"
```

---

## 🛡️ Escenarios Cubiertos

| Escenario | Qué pasa | Resultado |
|-----------|----------|-----------|
| **PC apaga durante Trader** | Persistencia guarda estado automáticamente | ✅ Estado intacto |
| **PC apaga antes de 08:00** | Scheduler ejecuta cuando arranca | ✅ Ejecución no se pierde |
| **Trader se cuelga/falla** | Watchdog lo detecta y reinicia | ✅ Operación continúa |
| **Dashboard se cuelga** | Watchdog lo reinicia | ✅ Monitoreo disponible |
| **Network se cae** | Trader sigue usando datos cacheados | ✅ Offline-ready |
| **Binance API falla** | Fallback a CSV cacheado | ✅ Datos disponibles |

---

## ⚙️ Configuración

**Interval de watchdog:** 5 minutos
```python
CHECK_INTERVAL = 300  # apps/watchdog_service.py:28
```

**Max edad del trader sin ejecutar:** 25 horas
```python
TRADER_MAX_AGE_HOURS = 25  # apps/watchdog_service.py:31
```

Si necesitas cambiar estos valores:
1. Edita `apps/watchdog_service.py`
2. Reinicia el watchdog: `python apps/watchdog_service.py`

---

## 📈 Logs Importantes

**Watchdog:** `logs/watchdog.log`
- Detecta qué procesos están corriendo
- Reinicia automáticos
- Errores de inicio

**Paper Trader:** `logs/paper_trader.log`
- Trades ejecutados
- Señales generadas
- Estado de riesgo
- Edge monitoring

**Dashboard:** `logs/dashboard.log`
- Errores de UI
- Queries a base de datos

---

## ✅ Estado Actual (2026-04-06)

```
Watchdog:        RUNNING (monitorea cada 5 min)
Trader:          RUNNING (ejecutó hoy 08:00)
Dashboard:       RUNNING (puerto 8501)
Persistence:     OK (todos los estados guardados)
Scheduler:       OK (tarea registrada para mañana 08:00)
```

**Próxima ejecución automática:** 2026-04-07 08:00

---

## 🚀 Para el Usuario

**No necesitas hacer nada.** El sistema es completamente automático:
- Watchdog monitorea continuamente ✅
- Trader ejecuta diariamente a las 08:00 ✅
- Si algo falla, se reinicia automáticamente ✅
- Dashboard muestra estado en tiempo real ✅

**Solo monitorea ocasionalmente:**
```bash
# Cada mañana: Ver si ejecutó hoy
tail -5 logs/paper_trader.log

# Cada semana: Ver edge health y PnL
# (Accede a http://localhost:8501)
```
