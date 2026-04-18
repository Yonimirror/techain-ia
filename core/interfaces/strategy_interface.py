from __future__ import annotations
from abc import ABC, abstractmethod

from core.domain.entities import Signal, MarketData, PortfolioState


class IStrategy(ABC):
    """
    Contract for all trading strategies.

    Strategies MUST be pure: no side effects, no I/O, no execution calls.
    They receive market data and return signals only.
    """

    @property
    @abstractmethod
    def strategy_id(self) -> str:
        """Unique identifier for this strategy."""
        ...

    @property
    @abstractmethod
    def version(self) -> str:
        """Strategy version string."""
        ...

    @abstractmethod
    def generate_signals(
        self,
        market_data: MarketData,
        portfolio_state: PortfolioState,
    ) -> list[Signal]:
        """
        Analyze market data and return a list of signals.

        Args:
            market_data: Current OHLCV data for the symbol.
            portfolio_state: Current portfolio snapshot (read-only).

        Returns:
            List of Signal objects. Empty list = no opinion.

        Constraints:
            - MUST be deterministic given same inputs.
            - MUST NOT execute trades, place orders, or mutate state.
            - MUST NOT perform I/O (network, disk).
        """
        ...

    @abstractmethod
    def warmup_period(self) -> int:
        """Minimum number of bars required before signals are valid."""
        ...

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(id={self.strategy_id}, v={self.version})"
