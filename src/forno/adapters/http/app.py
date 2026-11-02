"""FastAPI app factory. Wires routers, exception handlers, the RED
instrumentator, and correlation-ID middleware. See docs/api.md and
docs/observability.md "The pillars, wired together".
"""

from __future__ import annotations

import asyncio
import contextlib
import socket
import uuid
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from aiokafka import AIOKafkaConsumer
from fastapi import FastAPI, Request, Response
from prometheus_fastapi_instrumentator import Instrumentator

from forno.adapters.db.session import make_engine, make_session_factory
from forno.adapters.http.deps import DEV_API_KEY
from forno.adapters.http.exception_handlers import register_exception_handlers
from forno.adapters.http.routers import (
    health,
    kitchen,
    menu,
    orders,
    shifts,
    stations,
    stats,
    streams,
    tables,
)
from forno.adapters.messaging.sse_fanout import SseBroadcaster, SseFanoutConsumer
from forno.adapters.scheduling.naive import NaiveEstimator
from forno.domain.clock import SystemClock
from forno.observability import metrics
from forno.observability.context import bind_correlation_id
from forno.observability.logging import configure_logging, get_logger

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from starlette.middleware.base import RequestResponseEndpoint

    from forno.domain.ports import PromiseEstimator
    from forno.settings import Settings

logger = get_logger(__name__)

# Every replica needs to see every event (not a partition share), so `api`
# subscribes with its own ephemeral group and always reads from the log end.
# See docs/architecture.md "Why `api` also consumes".
_SSE_TOPICS = ("forno.orders.v1", "forno.kitchen.v1", "forno.menu.v1")


def _log_fanout_exit(task: asyncio.Task[None]) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error("api.sse_fanout_died", error=repr(exc))


def _make_estimator(name: str) -> PromiseEstimator:
    if name == "naive":
        return NaiveEstimator()
    msg = f"Unknown FORNO_PROMISE_ESTIMATOR: {name!r}"
    raise ValueError(msg)


def create_app(settings: Settings) -> FastAPI:
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = make_engine(settings.database_url)
        app.state.engine = engine
        app.state.session_factory = make_session_factory(engine)
        app.state.clock = SystemClock(time_scale=settings.time_scale)
        app.state.estimator = _make_estimator(settings.promise_estimator)
        app.state.broadcaster = SseBroadcaster(settings.sse_queue_size)
        app.state.settings = settings
        # Counters that may legitimately never fire: pre-register their
        # label sets so a dashboard shows "0", not "No data".
        metrics.idempotency_replays_total.labels(endpoint="/orders")
        metrics.sse_dropped_total.labels(reason="slow_consumer")
        for stream in ("kitchen", "service", "floor"):
            metrics.sse_connections.labels(stream=stream)

        group_id = f"sse-fanout-{socket.gethostname()}-{uuid.uuid4().hex[:8]}"
        consumer = AIOKafkaConsumer(
            *_SSE_TOPICS,
            bootstrap_servers=settings.kafka_bootstrap,
            group_id=group_id,
            auto_offset_reset="latest",
            enable_auto_commit=True,
        )
        fanout = SseFanoutConsumer(consumer, app.state.broadcaster)
        fanout_task = asyncio.create_task(fanout.run_forever())
        fanout_task.add_done_callback(_log_fanout_exit)
        # Surfaced by /health/ready: a dead fan-out means every SSE client
        # silently sees nothing but pings, which must not count as "ready".
        app.state.fanout_task = fanout_task
        logger.info("api.startup", sse_group_id=group_id)

        try:
            yield
        finally:
            fanout.stop()
            fanout_task.cancel()
            # CancelledError is the normal outcome here; anything else was
            # already reported by `_log_fanout_exit` when the task died.
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await fanout_task
            await engine.dispose()
            logger.info("api.shutdown")

    app = FastAPI(
        title="Forno",
        version="0.1.0",
        description=(
            "An event-driven pizzeria operations backend — see "
            "docs/architecture.md for the design, README.md to run it "
            "with `make up`.\n\n"
            "### Auth (dev stand-in)\n"
            f"Click **Authorize** above and set `X-API-Key: {DEV_API_KEY}` "
            "plus `X-Role`. `manager` can call *every* endpoint, including "
            "cashier/chef/waiter-only ones — the other three roles are "
            "restricted to their own slice of the API. Read-only `GET` "
            "endpoints need neither header. This is a deliberate stand-in "
            'for real auth — see docs/api.md "Conventions".\n\n'
            "### A walkthrough, role by role\n"
            "1. **manager** — `POST /menu/items`, `POST /stations` "
            "(`kind: oven`), `POST /tables`\n"
            "2. **cashier** — `POST /orders`; note `promised_at` in the "
            "response\n"
            "3. **chef** — `GET /kitchen/tickets` for the ticket/item ids, "
            "then `.../start` → `.../oven-in` → `.../ready` per item\n"
            "4. **waiter** — `POST /orders/{order_id}/items/{item_id}/delivered`\n"
            "5. **cashier** — `POST /orders/{order_id}/close`\n"
            "6. Check `GET /stats/summary` and `GET /metrics`\n\n"
            "Each tag below groups one role's endpoints; see docs/api.md "
            "for the full reference."
        ),
        openapi_tags=[
            {
                "name": "menu",
                "description": "manager — menu items: SKU, price, VAT, prep/bake times.",
            },
            {
                "name": "stations",
                "description": (
                    "manager — ovens and prep/pass stations. Defines the "
                    "kitchen capacity the PromiseEstimator reasons over."
                ),
            },
            {
                "name": "tables",
                "description": "manager creates tables; waiter seats/clears them.",
            },
            {
                "name": "shifts",
                "description": (
                    "manager — staff on duty. See the endpoint description "
                    "for what actually reads this today."
                ),
            },
            {
                "name": "orders",
                "description": (
                    "cashier places/cancels/closes orders and the waiter "
                    "delivers items; manager can void post-service."
                ),
            },
            {
                "name": "kitchen",
                "description": "chef — work tickets: start, oven-in, ready, scrap.",
            },
            {
                "name": "stats",
                "description": (
                    "Aggregated covers, revenue, ready time, breach/remake rate. No role required."
                ),
            },
            {
                "name": "streams",
                "description": "Server-Sent Events for live kitchen/service/floor updates.",
            },
            {"name": "health", "description": "Liveness/readiness probes."},
        ],
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def _correlation_id_middleware(
        request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        bind_correlation_id(request_id)
        response: Response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    register_exception_handlers(app)

    for router in (menu, stations, tables, shifts, orders, kitchen, stats, streams, health):
        app.include_router(router.router)

    Instrumentator().instrument(app).expose(app, endpoint="/metrics")

    return app
