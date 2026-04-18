# FORMATO DE REPORTE DE ESTADO

Cuando el usuario pide estado, usar este formato:

## 📊 ESTADO ACTUAL DEL PAPER TRADER

**Fecha:** [HOY]
**Capital:** $1,000,000 inicial

---

## 🏆 RESUMEN GENERAL

```
Total equity:         $X,XXX,XXX.XX
Return:               +X.XX%
Trades totales:       XX
Win rate:             XX%
Max drawdown:         X.XX%
Kill switch:          [ACTIVO/INACTIVO]
```

---

## 📈 POR ESTRATEGIA

### BTC 1d — reversion_rsi7_os35_ob60_sl5 (Sin EMA200)

**Últimas operaciones:**
- Operación 10: Compré en $X → Vendí en $X → PnL +$XXX (✅ ganancia)
- Operación 9: Compré en $X → Vendí en $X → PnL -$XXX (❌ pérdida)
- Operación 8: Compré en $X → Vendí en $X → PnL +$XXX (✅ ganancia)

**Resumen:**
```
Trades completados:   10/50
Win rate:             90%
Equity:               $1,050,059
Return:               +5.01%
Estado:               ✅ OK - Sin alertas
```

### BTC 1d — reversion_rsi7_os35_ob60_sl5_ema200 (Con EMA200)

**Últimas operaciones:**
- Operación 10: Compré en $X → Vendí en $X → PnL +$XXX (✅ ganancia)
- Operación 9: ...

**Resumen:**
```
Trades completados:   10/50
Win rate:             90%
Equity:               $1,050,059
Return:               +5.01%
Estado:               ✅ OK - Sin alertas
```

---

## 🎯 PRÓXIMOS PASOS

Trades hasta hito de validación: **40 restantes**
Estimación: **~5-8 semanas más**
Próxima ejecución trader: **Mañana 08:00**

---

## 📌 NOTAS IMPORTANTES

- **Kill switch activo?** Si está ON → mostrar razón (ej: "Daily loss limit: 5.09% >= 5.0%")
- **Divergencias detectadas?** Si edge health diverge >10% → avisar
- **Posiciones abiertas?** Si hay LONG activo → mostrar entrada, precio actual, PnL sin cerrar
