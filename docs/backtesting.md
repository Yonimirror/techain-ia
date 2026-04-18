# Backtesting

## Overview

The backtesting engine replays historical data bar-by-bar through the full trading pipeline.
This ensures backtest and live behavior use identical code paths.

## Modes

### 1. Simple Backtest

Runs the strategy over the full dataset in a single pass.

```bash
python -m apps.backtest_service.run_backtest \
    --symbol AAPL \
    --timeframe 1d \
    --start 2022-01-01 \
    --end 2023-12-31 \
    --strategy ema_crossover
```

### 2. Walk-Forward Validation

Splits data into sequential IS (in-sample) and OOS (out-of-sample) windows.

```
Total data: [===============================]
Split 1:    [---IS-1---][OOS-1]
Split 2:           [---IS-2---][OOS-2]
Split 3:                  [---IS-3---][OOS-3]
```

Each OOS window tests performance on unseen data, preventing overfitting.

```bash
python -m apps.backtest_service.run_backtest \
    --symbol AAPL --strategy ema_crossover \
    --walk-forward
```

### 3. Monte Carlo Simulation

Shuffles trade sequence N times to estimate outcome distribution.

**Answers:** Is the strategy's performance robust, or was it lucky with trade ordering?

```bash
python -m apps.backtest_service.run_backtest \
    --symbol AAPL --strategy ema_crossover \
    --monte-carlo --n-sims 1000
```

**Output metrics:**
- `sharpe_mean ± std`: Expected Sharpe across simulations
- `sharpe_p5`: Worst 5% Sharpe scenario
- `max_dd_p95`: Worst 5% drawdown scenario
- `P(loss)`: Probability of ending with negative return

## Metrics

| Metric | Formula | Good value |
|--------|---------|-----------|
| Total Return | (final - initial) / initial | > 15% |
| Sharpe Ratio | mean_excess_return / std | > 1.0 |
| Sortino Ratio | mean_excess / downside_std | > 1.5 |
| Max Drawdown | max(peak - trough) / peak | < 15% |
| Calmar Ratio | annual_return / max_dd | > 1.0 |
| Win Rate | wins / total_trades | > 50% |
| Profit Factor | sum(wins) / sum(losses) | > 1.5 |
| Expectancy | avg_pnl per trade | > 0 |

## Data Format

CSV files in `data/historical/` with naming: `{TICKER}_{TIMEFRAME}.csv`

Example: `data/historical/AAPL_1d.csv`

```csv
timestamp,open,high,low,close,volume
2022-01-03,182.01,182.88,177.71,182.01,104487900
2022-01-04,182.63,182.94,179.12,179.70,99310400
...
```

## Anti-Overfitting Rules

1. Always use Walk-Forward validation for final strategy evaluation
2. Use at least 3 OOS windows before trusting results
3. Monte Carlo P(loss) should be < 20% for a robust strategy
4. Never optimize parameters on the same data you evaluate on
5. Beware of Sharpe > 3.0 on daily data — likely overfitted
