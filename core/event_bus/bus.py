from __future__ import annotations
import asyncio
import logging
from collections import defaultdict
from typing import Awaitable, Callable, TypeVar

from core.event_bus.events import BaseEvent, EventType

logger = logging.getLogger(__name__)

Handler = Callable[[BaseEvent], Awaitable[None]]
T = TypeVar("T", bound=BaseEvent)


class EventBus:
    """
    Async publish/subscribe event bus.

    All system components communicate exclusively through this bus.
    No component holds direct references to other components.

    Usage:
        bus = EventBus()
        bus.subscribe(EventType.SIGNAL, my_handler)
        await bus.publish(SignalEvent(signal=...))
    """

    def __init__(self) -> None:
        self._handlers: dict[EventType, list[Handler]] = defaultdict(list)
        self._global_handlers: list[Handler] = []
        self._published_count: dict[EventType, int] = defaultdict(int)
        self._error_count: int = 0

    def subscribe(self, event_type: EventType, handler: Handler) -> None:
        """Register a handler for a specific event type."""
        self._handlers[event_type].append(handler)
        logger.debug("Subscribed %s to %s", handler.__qualname__, event_type.value)

    def subscribe_all(self, handler: Handler) -> None:
        """Register a handler that receives ALL event types (e.g., observability)."""
        self._global_handlers.append(handler)

    def unsubscribe(self, event_type: EventType, handler: Handler) -> None:
        """Remove a specific handler."""
        handlers = self._handlers[event_type]
        if handler in handlers:
            handlers.remove(handler)

    async def publish(self, event: BaseEvent) -> None:
        """
        Publish an event to all subscribers.

        Handlers are called concurrently. Errors in individual handlers
        are caught and logged — they NEVER suppress other handlers.
        """
        event_type = event.event_type
        self._published_count[event_type] += 1

        handlers = self._handlers[event_type] + self._global_handlers
        if not handlers:
            logger.debug("No handlers for event %s", event_type.value)
            return

        results = await asyncio.gather(
            *[self._safe_call(h, event) for h in handlers],
            return_exceptions=True,
        )

        for result in results:
            if isinstance(result, Exception):
                self._error_count += 1
                logger.error("Event handler error for %s: %s", event_type.value, result)

    async def _safe_call(self, handler: Handler, event: BaseEvent) -> None:
        try:
            await handler(event)
        except Exception as exc:
            logger.exception(
                "Handler %s failed on event %s: %s",
                handler.__qualname__,
                event.event_type.value,
                exc,
            )
            raise

    @property
    def stats(self) -> dict:
        return {
            "published": dict(self._published_count),
            "errors": self._error_count,
            "subscriptions": {k.value: len(v) for k, v in self._handlers.items()},
        }

    def reset_stats(self) -> None:
        self._published_count.clear()
        self._error_count = 0
