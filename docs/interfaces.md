# Interfaces

All core interfaces are defined in `core/interfaces/`.

## IStrategy

```python
class IStrategy(ABC):
    @property
    def strategy_id(self) -> str: ...

    @property
    def version(self) -> str: ...

    def generate_signals(
        self,
        market_data: MarketData,
        portfolio_state: PortfolioState,
    ) -> list[Signal]: ...

    def warmup_period(self) -> int: ...
```

**Rules:**
- MUST be deterministic
- MUST NOT execute trades or place orders
- MUST NOT perform I/O
- MUST NOT mutate PortfolioState

## IRiskEngine

```python
class IRiskEngine(ABC):
    def evaluate(
        self,
        signal: Signal,
        portfolio_state: PortfolioState,
    ) -> RiskDecision | RiskRejection: ...

    def activate_kill_switch(self, reason: str) -> None: ...
    def deactivate_kill_switch(self) -> None: ...
    def kill_switch_active(self) -> bool: ...
```

**Rules:**
- NEVER raises exceptions — always returns RiskDecision or RiskRejection
- Kill switch blocks ALL new trades immediately

## IExecutionEngine

```python
class IExecutionEngine(ABC):
    async def execute(self, order: Order) -> OrderResult: ...
    async def cancel(self, order: Order) -> bool: ...
    async def get_order_status(self, order: Order) -> Order: ...
```

## IDataProvider

```python
class IDataProvider(ABC):
    async def get_historical(...) -> MarketData: ...
    async def get_latest_bars(...) -> MarketData: ...
    async def get_current_price(symbol) -> Price: ...
    async def subscribe(...) -> None: ...
    async def unsubscribe(...) -> None: ...
```

## IBroker

```python
class IBroker(ABC):
    async def submit_order(order: Order) -> str: ...    # returns broker_order_id
    async def cancel_order(broker_order_id: str) -> bool: ...
    async def get_account_balance() -> Decimal: ...
    async def get_positions() -> dict[str, Quantity]: ...
    async def is_connected() -> bool: ...
```

## Adding a New Strategy

1. Create `core/strategies/my_strategy.py`
2. Inherit from `IStrategy`
3. Implement `generate_signals()` as a pure function
4. Define `warmup_period()`
5. Register in `config/strategies.yaml`
6. Write unit tests in `tests/unit/test_my_strategy.py`

## Adding a New Broker

1. Create `infrastructure/brokers/my_broker.py`
2. Inherit from `IBroker`
3. Implement all 5 async methods
4. Update `config/system.yaml` broker section
