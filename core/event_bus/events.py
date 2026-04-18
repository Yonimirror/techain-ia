from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from uuid import UUID, uuid4

from core.domain.entities import Signal, Order, MarketData
from core.interfaces.risk_interface import RiskDecision, RiskRejection


class EventType(str, Enum):
    MARKET_DATA = "MARKET_DATA"
    SIGNAL = "SIGNAL"
    RISK_APPROVED = "RISK_APPROVED"
    RISK_REJECTED = "RISK_REJECTED"
    ORDER_SUBMITTED = "ORDER_SUBMITTED"
    ORDER_FILLED = "ORDER_FILLED"
    ORDER_CANCELLED = "ORDER_CANCELLED"
    ORDER_REJECTED = "ORDER_REJECTED"
    TRADE_OPENED = "TRADE_OPENED"
    TRADE_CLOSED = "TRADE_CLOSED"
    KILL_SWITCH_ACTIVATED = "KILL_SWITCH_ACTIVATED"
    KILL_SWITCH_DEACTIVATED = "KILL_SWITCH_DEACTIVATED"
    SYSTEM_ERROR = "SYSTEM_ERROR"


@dataclass(frozen=True)
class BaseEvent:
    id: UUID = field(default_factory=uuid4)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def event_type(self) -> EventType:
        raise NotImplementedError


@dataclass(frozen=True)
class MarketDataEvent(BaseEvent):
    market_data: MarketData = field(default_factory=lambda: None)  # type: ignore
    execution_price: "Price | None" = None  # next bar open — set by backtester for realistic fills

    @property
    def event_type(self) -> EventType:
        return EventType.MARKET_DATA


@dataclass(frozen=True)
class SignalEvent(BaseEvent):
    signal: Signal = field(default_factory=lambda: None)  # type: ignore

    @property
    def event_type(self) -> EventType:
        return EventType.SIGNAL


@dataclass(frozen=True)
class RiskApprovedEvent(BaseEvent):
    decision: RiskDecision = field(default_factory=lambda: None)  # type: ignore

    @property
    def event_type(self) -> EventType:
        return EventType.RISK_APPROVED


@dataclass(frozen=True)
class RiskRejectedEvent(BaseEvent):
    rejection: RiskRejection = field(default_factory=lambda: None)  # type: ignore

    @property
    def event_type(self) -> EventType:
        return EventType.RISK_REJECTED


@dataclass(frozen=True)
class OrderSubmittedEvent(BaseEvent):
    order: Order = field(default_factory=lambda: None)  # type: ignore

    @property
    def event_type(self) -> EventType:
        return EventType.ORDER_SUBMITTED


@dataclass(frozen=True)
class OrderFilledEvent(BaseEvent):
    order: Order = field(default_factory=lambda: None)  # type: ignore

    @property
    def event_type(self) -> EventType:
        return EventType.ORDER_FILLED


@dataclass(frozen=True)
class KillSwitchEvent(BaseEvent):
    activated: bool = True
    reason: str = ""

    @property
    def event_type(self) -> EventType:
        return EventType.KILL_SWITCH_ACTIVATED if self.activated else EventType.KILL_SWITCH_DEACTIVATED


@dataclass(frozen=True)
class SystemErrorEvent(BaseEvent):
    error: str = ""
    component: str = ""

    @property
    def event_type(self) -> EventType:
        return EventType.SYSTEM_ERROR
