"""
Structured logging setup using structlog.

Outputs JSON logs in production, human-readable in development.
Every log entry includes: timestamp, level, component, event, and context.
"""
from __future__ import annotations
import logging
import sys
from enum import Enum

import structlog


class LogLevel(str, Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


def configure_logging(
    level: LogLevel = LogLevel.INFO,
    json_output: bool = True,
    log_file: str | None = None,
) -> None:
    """
    Configure structured logging for the entire system.

    Args:
        level: Minimum log level.
        json_output: True for JSON (production), False for human-readable (dev).
        log_file: Optional path to write logs to a file.
    """
    log_level = getattr(logging, level.value)

    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if log_file:
        handlers.append(logging.FileHandler(log_file))

    logging.basicConfig(
        format="%(message)s",
        level=log_level,
        handlers=handlers,
    )

    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if json_output:
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)  # type: ignore

    structlog.configure(
        processors=shared_processors + [
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processor=renderer,
        foreign_pre_chain=shared_processors,
    )

    for handler in handlers:
        handler.setFormatter(formatter)

    logging.getLogger("asyncio").setLevel(logging.WARNING)
