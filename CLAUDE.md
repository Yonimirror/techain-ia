# CLAUDE.md — Techain-IA Algorithmic Trading Engine

## Project Overview

Production-grade event-driven algorithmic trading engine in Python.

## Architecture

Event-driven. All components communicate via `EventBus`. No direct cross-component calls.

Full architecture: see [docs/architecture.md](docs/architecture.md)

## Absolute Rules (NEVER violate)

### 1. Never bypass the risk engine
Every trade MUST go through `IRiskEngine.evaluate()`.
No execution path exists that skips it.

### 2. Never execute without validation
Orders are only created after `RiskDecision` is returned.
`RiskRejection` means the trade does not happen.

### 3. Always log decisions
Every signal, approval, rejection, and fill must be traceable
via `DecisionTracer`. Use structured logging everywhere.

### 4. Keep modules decoupled
- Strategies do NOT call the risk engine
- Strategies do NOT call the execution engine
- Strategies do NOT perform I/O
- The risk engine does NOT call the execution engine
- All cross-component communication via EventBus

### 5. No hardcoded trading parameters
All strategy parameters, risk limits, and capital allocations
must be in `config/` YAML files. Zero hardcoded values in Python code.

### 6. Strategies must be pure functions
`generate_signals(market_data, portfolio_state) → list[Signal]`
- No side effects
- Deterministic
- No I/O
- No state mutation

## Development Commands

```bash
# Install dependencies
pip install -e ".[dev]"

# Run tests
pytest tests/ -v

# Run tests with coverage
pytest tests/ --cov=core --cov-report=term-missing

# Lint
ruff check .

# Type check
mypy core/

# Run research (find strategies)
python -m apps.research_service.run_research --assets BTC --timeframes 4h --years 3

# Run backtest
python -m apps.backtest_service.run_backtest --symbol BTC --timeframe 1d

# Run paper trader
python -m apps.trader_service.main --top 6 --capital 100000
```

## Project Structure

```
core/                 Core business logic
  domain/               Entities (Signal, Order, Trade, Position, MarketData, PortfolioState)
                        Value Objects (Price, Quantity, Symbol, Timeframe)
  strategies/           EMA Crossover, RSI Mean Reversion, indicators
  risk_engine/          Risk evaluation, EdgeMonitor (EWMA), position sizing
  backtesting/          BacktestRunner, WalkForwardRunner, metrics
  research/             Hypothesis generation, experiment runner, filters, repository
  decision_engine/      Signal → risk → execution orchestration
  execution_engine/     Order execution, PaperBroker
  portfolio_engine/     Portfolio state management
  market_regime/        ADX + ATR regime detection
  event_bus/            Async event bus
  interfaces/           Contracts (IStrategy, IRiskEngine, IDataProvider, IBroker)
apps/                 Runnable services
  trader_service/       Paper trading loop (daily automated via Windows scheduler)
  backtest_service/     CLI backtest runner
  research_service/     Research engine CLI
infrastructure/       External adapters
  data_providers/       BinanceDataProvider (live), CSVDataProvider (cache/offline)
config/               All configuration YAML files
observability/        Structured logging, Prometheus metrics, decision tracing
tests/                Unit and integration tests
docs/                 Architecture and API documentation
data/                 Historical CSVs, research DB, logs
```

## Adding Features

### New Strategy
1. Create `core/strategies/my_strategy.py` implementing `IStrategy`
2. Write unit tests in `tests/unit/test_my_strategy.py`
3. Add to `config/strategies.yaml`
4. Add hypothesis generation in `core/research/hypothesis.py`

### New Data Provider
1. Create `infrastructure/data_providers/my_provider.py` implementing `IDataProvider`
2. Export in `infrastructure/data_providers/__init__.py`
3. Update `config/system.yaml`

### New Risk Rule
1. Add to `core/risk_engine/engine.py` evaluation pipeline
2. Add `RejectionReason` enum value if needed
3. Document in `docs/risk_model.md`

## Testing Requirements

- Unit tests for ALL strategy indicator logic
- Unit tests for risk engine decision paths
- Integration test for full signal → fill pipeline
- Backtest must always run clean (no exceptions)
- No test should use real broker connections

## Key Docs

- [Architecture](docs/architecture.md)
- [Interfaces](docs/interfaces.md)
- [Risk Model](docs/risk_model.md)
- [Backtesting](docs/backtesting.md)
- [Observability](docs/observability.md)
