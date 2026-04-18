# Risk Model

## Philosophy

**Capital preservation comes before returns.**

The risk engine is the only gatekeeper between signal generation and order execution.
Every trade MUST be approved by it.

## Evaluation Pipeline

Every signal is evaluated in this exact order. The first failure returns a rejection.

```
1. Kill switch active?          → REJECT (KILL_SWITCH_ACTIVE)
2. Signal strength < min?       → REJECT (LOW_SIGNAL_STRENGTH)
3. Drawdown >= max_drawdown?    → REJECT + ACTIVATE KILL SWITCH
4. Trades today >= max?         → REJECT (EXCEEDS_POSITION_LIMIT)
5. Exposure >= max_exposure?    → REJECT (EXCEEDS_EXPOSURE_LIMIT)
6. Duplicate position?          → REJECT (DUPLICATE_SIGNAL)
7. Insufficient capital?        → REJECT (INSUFFICIENT_CAPITAL)
8. Compute position size        → APPROVE with quantity
```

## Position Sizing

### Fixed Fractional (default)

```
size = (equity * max_position_pct * signal_strength) / entry_price
```

Signal strength acts as a multiplier: a 0.5-strength signal gets half the max position size.

### Soft Drawdown Reduction

When drawdown exceeds `soft_drawdown_pct`, sizes scale down linearly:

```
reduction = 1 - (drawdown - soft_dd) / (max_dd - soft_dd)
effective_size = base_size * max(reduction, 0.1)
```

This means positions shrink to 10% of normal at max drawdown threshold.

### Kelly Criterion (optional)

```
f* = (p * b - q) / b
where:
  p = historical win rate
  b = avg_win / avg_loss
  q = 1 - p

actual_fraction = f* * kelly_fraction  (fractional Kelly, default 0.25)
actual_fraction = min(actual_fraction, kelly_max_pct / 100)
```

## Kill Switch

The kill switch is a hard stop that blocks ALL new trades.

**Auto-activates when:**
- `max_drawdown_pct` is breached

**Manual activation:**
- Risk engine: `risk_engine.activate_kill_switch(reason)`
- Deactivation requires explicit call: `risk_engine.deactivate_kill_switch()`

## Parameters (config/risk.yaml)

| Parameter | Default | Description |
|-----------|---------|-------------|
| max_position_size_pct | 5.0 | Max % equity per position |
| max_total_exposure_pct | 80.0 | Max % equity deployed |
| max_drawdown_pct | 15.0 | Kill switch threshold |
| soft_drawdown_pct | 10.0 | Start reducing sizes |
| kelly_fraction | 0.25 | Fractional Kelly multiplier |
| min_signal_strength | 0.3 | Minimum signal quality |
| max_trades_per_day | 20 | Daily trade limit |
| max_daily_loss_pct | 5.0 | Daily loss limit |
