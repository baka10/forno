"""SSE streams, role-scoped, documented in AsyncAPI. See docs/api.md
"SSE streams" and docs/architecture.md "Streaming to clients".
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Request
from sse_starlette.sse import EventSourceResponse

from forno.adapters.http.deps import Role, requires
from forno.adapters.http.sse import stream_events
from forno.adapters.http.wiring import get_broadcaster
from forno.adapters.messaging.sse_fanout import SseBroadcaster

router = APIRouter(prefix="/streams", tags=["streams"])


@router.get(
    "/kitchen",
    dependencies=[requires(Role.CHEF)],
    summary="Live kitchen ticket events (SSE)",
    description=(
        "`text/event-stream` of `ticket.item.state_changed` / `ticket.cancelled`. "
        "Not the source of truth — a client that "
        "drops the connection recovers state with `GET /kitchen/tickets`, "
        'not by replaying history. See docs/api.md "SSE streams". '
        "Swagger's Try it out does not render a live stream well; use "
        "`curl -N` or a browser `EventSource` instead."
    ),
)
async def kitchen_stream(
    request: Request,
    last_event_id: str | None = Header(default=None),
    broadcaster: SseBroadcaster = Depends(get_broadcaster),
) -> EventSourceResponse:
    return await stream_events(request, broadcaster, "kitchen", last_event_id)


@router.get(
    "/service",
    dependencies=[requires(Role.WAITER)],
    summary="Live front-of-house events (SSE)",
    description="`order.ready`, `item.ready`.",
)
async def service_stream(
    request: Request,
    last_event_id: str | None = Header(default=None),
    broadcaster: SseBroadcaster = Depends(get_broadcaster),
) -> EventSourceResponse:
    return await stream_events(request, broadcaster, "service", last_event_id)


@router.get(
    "/floor",
    dependencies=[requires(Role.MANAGER)],
    summary="Live floor/order events (SSE)",
    description="`order.placed`, `order.closed`.",
)
async def floor_stream(
    request: Request,
    last_event_id: str | None = Header(default=None),
    broadcaster: SseBroadcaster = Depends(get_broadcaster),
) -> EventSourceResponse:
    return await stream_events(request, broadcaster, "floor", last_event_id)
