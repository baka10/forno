"""structlog configuration: JSON to stdout, one event per line, no log
files. See docs/observability.md "Logging".

Rules that keep logs usable: stable dotted event names (`item.oven_in`),
never interpolated prose; context bound once per request/message and
inherited; INFO for domain transitions, ERROR only when a human might need
to act.
"""

from __future__ import annotations

import logging
import sys

import structlog

from forno.observability.context import get_correlation_id


def _inject_correlation_id(
    logger: object, method_name: str, event_dict: dict[str, object]
) -> dict[str, object]:
    del logger, method_name
    correlation_id = get_correlation_id()
    if correlation_id is not None:
        event_dict.setdefault("correlation_id", correlation_id)
    return event_dict


def configure_logging(level: str) -> None:
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=numeric_level)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            _inject_correlation_id,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True, key="ts"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(numeric_level),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
