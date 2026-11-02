"""Human-readable logs for watching the simulator run — deliberately not the
JSON-to-stdout convention `forno.observability.logging` uses for the API
processes (the simulator can't import `forno` at all, see
docs/adr/0008-simulator-as-external-client.md, and this is a demo tool
eyeballed via `docker logs -f simulator`, not aggregated log tooling).

One INFO line per actor decision — order placed, item started/ready/scrapped,
item delivered, customer served/walked out — never per HTTP call, retry, or
poll. That noise already lives in the shadow ledger's request counts.
"""

from __future__ import annotations

import logging
import sys

import structlog


def configure_logging(level: str = "INFO") -> None:
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=numeric_level)

    # httpx/httpcore log every request at INFO on the stdlib `logging` root
    # config above — exactly the low-level noise this module exists to keep
    # out. One line per actor decision, not one per HTTP call.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="%H:%M:%S", utc=True),
            structlog.dev.ConsoleRenderer(colors=sys.stdout.isatty()),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(numeric_level),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
