"""The `api` process's own consumer: ephemeral, replica-unique group
(`sse-fanout-{hostname}-{boot_id}`), always reads from the log end, and
broadcasts to in-process asyncio subscriber queues. See
docs/architecture.md "Why `api` also consumes" and "Streaming to clients".
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import TYPE_CHECKING, Any

from forno.observability import metrics
from forno.observability.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Sequence

    from aiokafka import AIOKafkaConsumer
    from aiokafka.structs import ConsumerRecord

logger = get_logger(__name__)


class SseBroadcaster:
    """Bounded per-subscriber queue. On overflow, the connection is closed
    with a `stream.desync` event rather than applying backpressure to the
    whole fan-out. See docs/architecture.md "Streaming to clients"."""

    def __init__(self, queue_size: int) -> None:
        self._queue_size = queue_size
        self._subscribers: dict[str, list[asyncio.Queue[dict[str, Any]]]] = {}
        self._next_id = 0

    def subscribe(self, stream: str) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=self._queue_size)
        self._subscribers.setdefault(stream, []).append(queue)
        metrics.sse_connections.labels(stream=stream).inc()
        return queue

    def unsubscribe(self, stream: str, queue: asyncio.Queue[dict[str, Any]]) -> None:
        subscribers = self._subscribers.get(stream, [])
        if queue in subscribers:
            subscribers.remove(queue)
            metrics.sse_connections.labels(stream=stream).dec()

    async def publish(self, stream: str, event: dict[str, Any]) -> None:
        """Non-blocking put; overflow drops the subscriber, not the fan-out."""
        self._next_id += 1
        # sse_starlette's ServerSentEvent regexes on `id` — it must be a
        # string, not the bare int this counter is.
        envelope = {"id": str(self._next_id), **event}
        for queue in list(self._subscribers.get(stream, [])):
            try:
                queue.put_nowait(envelope)
            except asyncio.QueueFull:
                self.unsubscribe(stream, queue)
                # `unsubscribe` only removes `queue` from the subscriber
                # list — it's still full, so make room before the desync
                # notice's own put_nowait, or it raises QueueFull too.
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
                with contextlib.suppress(asyncio.QueueFull):
                    queue.put_nowait(
                        {"id": str(self._next_id), "event": "stream.desync", "data": "{}"}
                    )
                metrics.sse_dropped_total.labels(reason="slow_consumer").inc()


def _header(headers: Sequence[tuple[str, bytes]] | None, name: str) -> str | None:
    for key, value in headers or ():
        if key == name:
            return value.decode()
    return None


def _routes(event_type: str, payload: dict[str, Any]) -> list[tuple[str, str]]:
    """Which SSE stream(s) an event fans out to, and under what event name.
    See docs/api.md "SSE streams"."""
    if event_type == "OrderPlaced":
        return [("floor", "order.placed")]
    if event_type == "OrderClosed":
        return [("floor", "order.closed")]
    if event_type == "OrderCancelled":
        return [("kitchen", "ticket.cancelled")]
    if event_type == "OrderReady":
        return [("service", "order.ready")]
    if event_type == "ItemStateChanged":
        routes = [("kitchen", "ticket.item.state_changed")]
        if payload.get("status") in ("READY", "DELIVERED"):
            routes.append(("service", "item.ready"))
        return routes
    return []


class SseFanoutConsumer:
    def __init__(self, consumer: AIOKafkaConsumer, broadcaster: SseBroadcaster) -> None:
        self._consumer = consumer
        self._broadcaster = broadcaster
        self._stopping = asyncio.Event()

    def stop(self) -> None:
        self._stopping.set()

    async def run_forever(self) -> None:
        await self._consumer.start()
        try:
            async for record in self._consumer:
                if self._stopping.is_set():
                    break
                await self._handle(record)
        finally:
            await self._consumer.stop()

    async def _handle(self, record: ConsumerRecord) -> None:
        event_type = _header(record.headers, "event_type")
        if event_type is None:
            return
        try:
            payload = json.loads(record.value)
        except (ValueError, TypeError):
            logger.warning("sse_fanout.invalid_payload", topic=record.topic)
            return

        for stream, name in _routes(event_type, payload):
            await self._broadcaster.publish(stream, {"event": name, "data": json.dumps(payload)})
