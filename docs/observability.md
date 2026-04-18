# Observability

## Structured Logging

All logs are structured JSON (production) or human-readable (development).

Every log entry includes:
- `timestamp`: ISO 8601
- `level`: DEBUG/INFO/WARNING/ERROR/CRITICAL
- `logger`: component name
- `event`: what happened
- context fields (symbol, order_id, pnl, etc.)

### Configure

```python
from observability import configure_logging, LogLevel

# Production (JSON)
configure_logging(LogLevel.INFO, json_output=True, log_file="data/logs/system.log")

# Development (human-readable)
configure_logging(LogLevel.DEBUG, json_output=False)
```

## Metrics

The `MetricsCollector` tracks:

| Metric | Description |
|--------|-------------|
| `trades.total` | Total trades executed |
| `trades.win_rate_pct` | Win rate |
| `trades.total_pnl` | Cumulative realized PnL |
| `portfolio.current_equity` | Current equity |
| `portfolio.max_drawdown_pct` | Peak drawdown |
| `signals_by_strategy` | Signals generated per strategy |
| `rejections_by_reason` | Risk rejections grouped by reason |
| `event_counts` | Events by type |

```python
from observability import MetricsCollector

metrics = MetricsCollector()
snapshot = metrics.snapshot()
print(snapshot)
```

## Decision Tracer

Every trading decision is recorded with full context:

```
Signal(AAPL | LONG | strength=0.72)
  → Risk: APPROVED | qty=54.3 | score=0.12
  → Order: MARKET BUY 54.3 AAPL
  → Fill: $182.45 | fees=$0.99
```

### Read decision traces

```python
from observability import DecisionTracer

tracer = DecisionTracer(log_file="data/logs/decisions.jsonl")

# All approved trades
approved = tracer.get_approved()

# Why were trades rejected?
tracer.rejection_summary()
# → {"LOW_SIGNAL_STRENGTH": 42, "DUPLICATE_SIGNAL": 18, ...}
```

## Log Levels by Component

| Component | Level | Reason |
|-----------|-------|--------|
| Kill switch | CRITICAL | Always notify |
| Risk rejections | INFO | Normal operation |
| Trade fills | INFO | Business event |
| Signal generation | DEBUG | High volume |
| Event bus | DEBUG | Very high volume |
