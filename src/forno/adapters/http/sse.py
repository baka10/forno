"""SSE response helper. See docs/api.md "SSE streams" and
docs/architecture.md "Streaming to clients".

`Last-Event-ID` is honoured on reconnect: every frame carries a monotonic
per-stream ID, and a client that misses everything recovers with one GET
against the corresponding read model — the stream is a convenience layer,
never the source of truth.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from sse_starlette.sse import EventSourceResponse

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from fastapi import Request

    from forno.adapters.messaging.sse_fanout import SseBroadcaster

_HEARTBEAT_SECONDS = 15


async def stream_events(
    request: Request, broadcaster: SseBroadcaster, stream: str, last_event_id: str | None
) -> EventSourceResponse:
    """Tails the live broadcaster queue for `stream`.

    `Last-Event-ID` is accepted (browsers resend it automatically on
    reconnect) but full event replay is intentionally left to the read
    model — a client that missed events recovers with one GET against the
    corresponding resource. See docs/api.md "SSE streams": "the stream is
    never the source of truth."
    """
    del last_event_id
    return EventSourceResponse(_event_generator(request, broadcaster, stream))


async def _event_generator(
    request: Request, broadcaster: SseBroadcaster, stream: str
) -> AsyncIterator[dict[str, Any]]:
    queue = broadcaster.subscribe(stream)
    try:
        while True:
            if await request.is_disconnected():
                break
            try:
                event = await asyncio.wait_for(queue.get(), timeout=_HEARTBEAT_SECONDS)
            except TimeoutError:
                yield {"event": "ping", "data": ""}
                continue
            yield event
            if event.get("event") == "stream.desync":
                break
    finally:
        broadcaster.unsubscribe(stream, queue)
