"""Structured logging setup (structlog). JSON in CI/containers, pretty output locally."""

import logging
import sys
from typing import Any

import structlog


def configure_logging(level: str = "INFO", fmt: str = "console") -> None:
    """Configure structlog once for the whole process."""
    renderer: Any = (
        structlog.processors.JSONRenderer()
        if fmt == "json"
        else structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level)),
        logger_factory=_stderr_logger,
        cache_logger_on_first_use=False,
    )


def _stderr_logger(*_args: Any) -> structlog.PrintLogger:
    """Resolve sys.stderr at log time, so redirected/replaced streams are respected."""
    return structlog.PrintLogger(sys.stderr)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return a logger bound with the component name."""
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(component=name)
    return logger
