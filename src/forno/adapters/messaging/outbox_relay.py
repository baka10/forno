"""The relay: poll -> publish -> mark sent. About sixty lines by design —
see docs/adr/0002-transactional-outbox.md.

    SELECT * FROM outbox WHERE sent_at IS NULL
    ORDER BY id FOR UPDATE SKIP LOCKED LIMIT :batch;

`FOR UPDATE SKIP LOCKED` makes this horizontally scalable with no coordination.
Runs as the `relay` process role (`FORNO_ROLE=relay`, or `forno relay` command).
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import func, select

from forno.adapters.db.models import OutboxModel
from forno.observability import metrics
from forno.observability.logging import get_logger

if TYPE_CHECKING:
    from aiokafka import AIOKafkaProducer
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = get_logger(__name__)


class OutboxRelay:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        producer: AIOKafkaProducer,
        poll_ms: int,
        batch_size: int,
    ) -> None:
        self._session_factory = session_factory
        self._producer = producer
        self._poll_ms = poll_ms
        self._batch_size = batch_size
        self._stopping = asyncio.Event()
        self._last_poll_failed = False

    def stop(self) -> None:
        self._stopping.set()

    async def run_forever(self) -> None:
        while not self._stopping.is_set():
            start = asyncio.get_event_loop().time()
            published = await self._poll_once()
            metrics.relay_batch_seconds.observe(asyncio.get_event_loop().time() - start)
            if published == 0 or self._last_poll_failed:
                # Nothing to do, or the broker just refused us: back off
                # rather than spin. A publish failure is a reason to wait and
                # retry, not to exit the process and restart-loop the container.
                await asyncio.sleep(self._poll_ms / 1000)

    async def _poll_once(self) -> int:
        """Returns the number of rows published. Keyed by `order_id` for
        per-aggregate ordering — see docs/architecture.md "Topics and
        partitioning"."""
        async with self._session_factory() as session, session.begin():
            pending_stmt = select(func.count(), func.min(OutboxModel.created_at)).where(
                OutboxModel.sent_at.is_(None)
            )
            pending_count, oldest_created_at = (await session.execute(pending_stmt)).one()
            metrics.outbox_pending.set(pending_count)
            metrics.outbox_lag_seconds.set(
                (datetime.now(UTC) - oldest_created_at).total_seconds()
                if oldest_created_at is not None
                else 0
            )

            batch_stmt = (
                select(OutboxModel)
                .where(OutboxModel.sent_at.is_(None))
                .order_by(OutboxModel.id)
                .limit(self._batch_size)
                .with_for_update(skip_locked=True)
            )
            rows = (await session.execute(batch_stmt)).scalars().all()
            if not rows:
                return 0

            now = datetime.now(UTC)
            published = 0
            self._last_poll_failed = False
            for row in rows:
                try:
                    await self._producer.send_and_wait(
                        row.topic,
                        key=str(row.aggregate_id).encode(),
                        value=json.dumps(row.payload).encode(),
                        headers=[
                            ("correlation_id", row.correlation_id.encode()),
                            ("event_type", row.event_type.encode()),
                        ],
                    )
                except Exception:
                    metrics.outbox_published_total.labels(result="error").inc()
                    logger.exception("outbox.publish_failed", outbox_id=row.id, topic=row.topic)
                    # Stop at the first failure so per-aggregate order is kept:
                    # rows already published in this batch are marked sent on
                    # commit, this one and everything after it stay pending and
                    # are retried on the next poll, in id order.
                    self._last_poll_failed = True
                    break
                metrics.outbox_published_total.labels(result="ok").inc()
                row.sent_at = now
                published += 1

            return published
