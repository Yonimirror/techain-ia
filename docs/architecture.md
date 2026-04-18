# Architecture

## Overview

Techain-IA is an event-driven, modular algorithmic trading engine.
All components communicate exclusively via an **EventBus** — no direct calls between subsystems.

## Event Flow

```
External Data
     │
     ▼
[DataProvider]
     │  MarketDataEvent
     ▼
[DecisionEngine] ──────────────────────────────────────────────────┐
     │                                                             │
     │  calls all IStrategy.generate_signals()                    │
     │  (pure functions, no side effects)                         │
     ▼                                                            │
[Strategies] → [SignalEvent]                                      │
     │                                                             │
     ▼                                                             │
[RiskEngine.evaluate()]                                            │
     │                                                             │
     ├──[REJECTED]──▶ RiskRejectedEvent ──────────────────────────┤
     │                                                             │
     └──[APPROVED]──▶ RiskApprovedEvent                           │
                           │                                       │
                           ▼                                       │
                    [ExecutionEngine]                               │
                           │  OrderSubmittedEvent                  │
                           ▼                                       │
                      [IBroker]                                     │
                           │  OrderFilledEvent                     │
                           ▼                                       │
                    [PortfolioEngine]  ◄──────────────────────────┘
                           │
                           ▼
                    [PortfolioState updated]
                           │
                           ▼
                    [MetricsCollector]
                    [DecisionTracer]
```

## Components

| Component | Responsibility | Allowed Dependencies |
|-----------|---------------|----------------------|
| Strategy | Generate signals from market data | Domain only |
| RiskEngine | Approve/reject signals, size positions | Domain, Config |
| DecisionEngine | Orchestrate signal → risk → execution | All via EventBus |
| ExecutionEngine | Submit orders to broker | IBroker, Domain |
| PortfolioEngine | Track positions, PnL, equity | Domain |
| EventBus | Decouple all components | None |
| DataProvider | Fetch OHLCV market data | Domain, Infrastructure |

## Key Design Decisions

### 1. Event-Driven Architecture
No component holds references to other components. All communication is via events.
This enables: parallel processing, easy testing, component replacement.

### 2. Immutable Domain Objects
`Signal`, `OHLCV`, `Price`, `Quantity` are frozen dataclasses.
Prevents accidental mutation of trading-critical data.

### 3. Pure Strategy Functions
Strategies receive data, return signals, nothing else.
No I/O, no state mutation, no execution calls.
This makes them trivially testable and auditable.

### 4. Risk Engine as Gatekeeper
Every trade MUST pass through the risk engine.
No execution path bypasses it.
Kill switch immediately blocks all new trades.

### 5. Configuration Externalized
All parameters in YAML files under `/config/`.
Zero hardcoded trading parameters in Python code.
