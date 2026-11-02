"""The correlation-ID chain: one `X-Request-ID` crosses every boundary. See
docs/observability.md "The pillars, wired together".

    X-Request-ID -> contextvar -> structlog processor      (every log line)
                                -> outbox.correlation_id    (durable)
                                -> Kafka message header     (async boundary)
                                -> consumer structlog context (rehydrated)
                                -> SSE frame                (visible to client)

Never a metric label — reaches metrics only as an exemplar attached to a
histogram bucket.
"""

from __future__ import annotations

from contextvars import ContextVar

correlation_id_var: ContextVar[str | None] = ContextVar("correlation_id", default=None)


def get_correlation_id() -> str | None:
    return correlation_id_var.get()


def bind_correlation_id(correlation_id: str) -> None:
    correlation_id_var.set(correlation_id)
