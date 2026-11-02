"""Consumer base + the two projectors. See docs/architecture.md "Containers"
(stable, shared consumer groups: `kitchen`, `stats`) and "Reliability
machinery" for the retry/DLQ classification.

Failure classification (docs/architecture.md "Reliability machinery"):
    retryable    — broker/DB blip, timeout: automatic backoff, then DLQ if exhausted
    poison       — malformed or unknown schema_version: never retried, DLQ immediately, page
    caller_error — event violates a domain invariant: DLQ, alert — a bug upstream
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import func, select

from forno.adapters.db.models import KitchenTicketModel, StatsOrderModel, TicketLineModel
from forno.adapters.messaging.inbox import mark_processed_or_skip
from forno.domain.exceptions import DomainError
from forno.domain.order import Channel
from forno.observability import metrics
from forno.observability.context import bind_correlation_id
from forno.observability.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Sequence

    from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
    from aiokafka.structs import ConsumerRecord, TopicPartition
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = get_logger(__name__)

# How long an idle poll waits before re-checking `stop()`; bounds SIGTERM latency.
_POLL_TIMEOUT_MS = 1000
# Pause before re-reading a record whose effect *and* DLQ publish both failed.
_FAILURE_BACKOFF_SECONDS = 2.0

_OUTCOME_BY_EVENT: dict[str, str] = {
    "OrderClosed": "served",
    "OrderCancelled": "cancelled",
    "OrderVoided": "voided",
    "OrderRejected": "rejected",
}


class DlqClassification(StrEnum):
    RETRYABLE = "retryable"
    POISON = "poison"
    CALLER_ERROR = "caller_error"


def _header(headers: Sequence[tuple[str, bytes]] | None, name: str) -> str | None:
    for key, value in headers or ():
        if key == name:
            return value.decode()
    return None


class _PoisonError(ValueError):
    """The record can never be applied: malformed body or envelope."""


def _decode_envelope(
    value: bytes | None, event_type: str | None
) -> tuple[str, dict[str, Any], UUID, datetime]:
    """JSON body -> (event_type, payload, event_id, occurred_at). Anything
    missing or unparseable is poison, not a transient failure worth retrying."""
    try:
        payload = json.loads(value or b"")
    except (ValueError, TypeError) as exc:
        msg = "invalid json body"
        raise _PoisonError(msg) from exc
    if event_type is None or not isinstance(payload, dict) or payload.get("schema_version") != 1:
        msg = "missing/unknown schema_version"
        raise _PoisonError(msg)
    try:
        return (
            event_type,
            payload,
            UUID(payload["event_id"]),
            datetime.fromisoformat(payload["occurred_at"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        msg = f"bad envelope: {exc}"
        raise _PoisonError(msg) from exc


class Consumer:
    """Shared consume -> inbox-check -> apply -> commit-offset loop.

    Subclasses (`KitchenProjector`, `StatsProjector`) supply `group_id`,
    `topics`, and `apply_event`.
    """

    group_id: str
    topics: tuple[str, ...]

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        consumer: AIOKafkaConsumer,
        dlq_producer: AIOKafkaProducer,
        max_retries: int,
    ) -> None:
        self._session_factory = session_factory
        self._consumer = consumer
        self._dlq_producer = dlq_producer
        self._max_retries = max_retries
        self._stopping = asyncio.Event()

    def stop(self) -> None:
        self._stopping.set()

    async def run_forever(self) -> None:
        """Poll -> handle -> commit offset, in that order.

        Offsets are committed by hand *after* the record's effect is
        durable (inbox row + read-model change in one DB transaction, or a
        DLQ publish). With auto-commit the fetched position would be
        committed on a timer while a record was still in its retry loop, and
        a crash in that window would lose the event for good. Polling with
        `getmany` (rather than `async for`) is what lets an idle projector
        notice `stop()` and exit on SIGTERM instead of blocking forever on
        the next record."""
        await self._consumer.start()
        # A DLQ that stays empty should read "0" on the dashboard, not "No data".
        for topic in self.topics:
            for classification in DlqClassification:
                metrics.dlq_messages_total.labels(topic=topic, classification=classification.value)
        try:
            while not self._stopping.is_set():
                batches = await self._consumer.getmany(timeout_ms=_POLL_TIMEOUT_MS)
                for tp, records in batches.items():
                    await self._handle_partition_batch(tp, records)
                    if self._stopping.is_set():
                        break
        finally:
            await self._consumer.stop()

    async def _handle_partition_batch(
        self, tp: TopicPartition, records: Sequence[ConsumerRecord]
    ) -> None:
        next_offset: int | None = None
        for record in records:
            try:
                await self._handle(record)
            except Exception:
                # Neither the effect nor the DLQ publish went through (broker
                # or DB down for longer than the retry budget). Commit what
                # did succeed, rewind to this record and come back to it —
                # never skip past an unprocessed offset.
                logger.exception("consumer.record_failed", topic=record.topic, offset=record.offset)
                if next_offset is not None:
                    await self._consumer.commit({tp: next_offset})
                self._consumer.seek(tp, record.offset)
                await asyncio.sleep(_FAILURE_BACKOFF_SECONDS)
                return
            next_offset = record.offset + 1
            if self._stopping.is_set():
                break
        if next_offset is not None:
            await self._consumer.commit({tp: next_offset})
            self._observe_lag(tp, next_offset)

    def _observe_lag(self, tp: TopicPartition, next_offset: int) -> None:
        """Committed position vs. the partition's high-water mark, straight
        from the consumer's own fetch metadata — no broker exporter needed."""
        highwater = self._consumer.highwater(tp)
        if highwater is None:
            return
        metrics.consumer_lag_messages.labels(
            group=self.group_id, topic=tp.topic, partition=str(tp.partition)
        ).set(max(highwater - next_offset, 0))

    async def _handle(self, record: ConsumerRecord) -> None:
        correlation_id = _header(record.headers, "correlation_id") or "-"
        bind_correlation_id(correlation_id)

        try:
            event_type, payload, event_id, occurred_at = _decode_envelope(
                record.value, _header(record.headers, "event_type")
            )
        except _PoisonError as exc:
            await self._to_dlq(record, DlqClassification.POISON, str(exc))
            return

        attempt = 0
        while True:
            try:
                async with self._session_factory() as session, session.begin():
                    first_delivery = await mark_processed_or_skip(session, event_id, self.group_id)
                    if not first_delivery:
                        metrics.events_consumed_total.labels(
                            topic=record.topic, consumer=self.group_id, result="duplicate"
                        ).inc()
                        return
                    await self.apply_event(session, event_type, payload)
            except DomainError as exc:
                metrics.events_consumed_total.labels(
                    topic=record.topic, consumer=self.group_id, result="failed"
                ).inc()
                await self._to_dlq(record, DlqClassification.CALLER_ERROR, str(exc))
                return
            except Exception as exc:
                attempt += 1
                metrics.events_consumed_total.labels(
                    topic=record.topic, consumer=self.group_id, result="failed"
                ).inc()
                if attempt > self._max_retries:
                    logger.exception(
                        "consumer.retries_exhausted", error=str(exc), event_type=event_type
                    )
                    await self._to_dlq(record, DlqClassification.RETRYABLE, str(exc))
                    return
                await asyncio.sleep(min(2**attempt, 30))
            else:
                metrics.projection_lag_seconds.labels(projector=self.group_id).set(
                    (datetime.now(UTC) - occurred_at).total_seconds()
                )
                metrics.events_consumed_total.labels(
                    topic=record.topic, consumer=self.group_id, result="applied"
                ).inc()
                return

    async def _to_dlq(
        self, record: ConsumerRecord, classification: DlqClassification, reason: str
    ) -> None:
        metrics.dlq_messages_total.labels(
            topic=record.topic, classification=classification.value
        ).inc()
        logger.warning(
            "consumer.dlq", topic=record.topic, classification=classification.value, reason=reason
        )
        await self._dlq_producer.send_and_wait(
            f"{record.topic}.dlq",
            key=record.key,
            value=record.value,
            headers=[
                *(record.headers or []),
                ("dlq_classification", classification.value.encode()),
                ("dlq_reason", reason.encode()[:200]),
            ],
        )

    async def apply_event(
        self, session: AsyncSession, event_type: str, payload: dict[str, Any]
    ) -> None:
        raise NotImplementedError


_STAGE_BY_STATUS: dict[str, str] = {
    "QUEUED": "queue",
    "PREPPING": "prep",
    "IN_OVEN": "oven",
    "READY": "pass",
}


class KitchenProjector(Consumer):
    """Order events -> `kitchen_tickets` / `ticket_lines` read model. Stateless
    and replayable — deleting the read model and resetting the consumer group
    rebuilds it exactly. See docs/adr/0006-kitchen-tickets-as-projection.md."""

    group_id = "kitchen"
    topics = ("forno.orders.v1",)

    async def apply_event(
        self, session: AsyncSession, event_type: str, payload: dict[str, Any]
    ) -> None:
        if event_type == "OrderPlaced":
            await self._on_order_placed(session, payload)
        elif event_type == "ItemStateChanged":
            await self._on_item_state_changed(session, payload)
        elif event_type in {"OrderCancelled", "OrderRejected"}:
            await self._on_ticket_closed(session, payload, "CANCELLED")
        elif event_type == "OrderVoided":
            await self._on_ticket_closed(session, payload, "VOIDED")
        elif event_type == "OrderReady":
            await self._on_ticket_closed(session, payload, "READY")
        await _refresh_kitchen_gauges(session)

    async def _on_order_placed(self, session: AsyncSession, payload: dict[str, Any]) -> None:
        ticket = KitchenTicketModel(
            order_id=UUID(payload["order_id"]),
            status="OPEN",
            created_at=datetime.fromisoformat(payload["occurred_at"]),
        )
        session.add(ticket)
        await session.flush()
        for line in payload["items"]:
            session.add(
                TicketLineModel(
                    ticket_id=ticket.id,
                    order_item_id=UUID(line["order_item_id"]),
                    station="prep",
                    status="QUEUED",
                    status_changed_at=ticket.created_at,
                )
            )

    async def _on_item_state_changed(self, session: AsyncSession, payload: dict[str, Any]) -> None:
        item_id = UUID(payload["item_id"])
        occurred_at = datetime.fromisoformat(payload["occurred_at"])
        line = (
            await session.execute(
                select(TicketLineModel).where(TicketLineModel.order_item_id == item_id)
            )
        ).scalar_one_or_none()
        status = payload["status"]
        station = (
            "oven"
            if status == "IN_OVEN"
            else "pass"
            if status in ("READY", "DELIVERED")
            else "prep"
        )

        if line is None:
            # A remake: a brand new order_item_id with no line from OrderPlaced.
            # `ticket_id` on the wire event is how it finds its ticket.
            ticket_id = payload.get("ticket_id")
            if ticket_id is None:
                return
            session.add(
                TicketLineModel(
                    ticket_id=UUID(ticket_id),
                    order_item_id=item_id,
                    station=station,
                    status=status,
                    status_changed_at=occurred_at,
                )
            )
            return

        stage = _STAGE_BY_STATUS.get(line.status)
        if stage is not None:
            elapsed = (occurred_at - line.status_changed_at).total_seconds()
            metrics.item_stage_seconds.labels(stage=stage).observe(max(elapsed, 0.0))

        line.status = status
        line.station = station
        line.status_changed_at = occurred_at

    async def _on_ticket_closed(
        self, session: AsyncSession, payload: dict[str, Any], status: str
    ) -> None:
        ticket = (
            await session.execute(
                select(KitchenTicketModel).where(
                    KitchenTicketModel.order_id == UUID(payload["order_id"])
                )
            )
        ).scalar_one_or_none()
        if ticket is not None:
            ticket.status = status


async def _refresh_kitchen_gauges(session: AsyncSession) -> None:
    """`forno_kitchen_oldest_ticket_age_seconds` / `forno_kitchen_tickets_open`
    — age, not depth, is the SLI (docs/observability.md "Kitchen"). Recomputed
    on every projected event rather than by a separate poller, which keeps the
    read model as the single source these gauges are derived from."""
    now = datetime.now(UTC)

    open_counts = (
        await session.execute(
            select(TicketLineModel.station, TicketLineModel.status, func.count())
            .join(KitchenTicketModel, KitchenTicketModel.id == TicketLineModel.ticket_id)
            .where(KitchenTicketModel.status == "OPEN")
            .group_by(TicketLineModel.station, TicketLineModel.status)
        )
    ).all()
    for station, status, count in open_counts:
        metrics.kitchen_tickets_open.labels(station=station, status=status).set(count)

    oldest_by_station = (
        await session.execute(
            select(TicketLineModel.station, func.min(KitchenTicketModel.created_at))
            .join(KitchenTicketModel, KitchenTicketModel.id == TicketLineModel.ticket_id)
            .where(
                KitchenTicketModel.status == "OPEN",
                TicketLineModel.status.notin_(("READY", "DELIVERED")),
            )
            .group_by(TicketLineModel.station)
        )
    ).all()
    for station, oldest in oldest_by_station:
        age = (now - oldest).total_seconds() if oldest is not None else 0.0
        metrics.kitchen_oldest_ticket_age_seconds.labels(station=station).set(age)


async def _refresh_open_orders_gauge(session: AsyncSession) -> None:
    """`forno_orders_open` — WIP, not a rate. See CONTRIBUTING.md "Adding a
    metric": answers "is the kitchen/floor falling behind?" as it happens,
    instead of only after the fact via `forno_orders_total`'s outcome
    counters. Recomputed on every projected event, same as the kitchen
    gauges — the read model stays the single source.

    Sets every `Channel` explicitly, including to 0 — a channel with no
    open orders would otherwise just never appear in the GROUP BY, and a
    Gauge holds its last value rather than resetting, silently pinning the
    dashboard at a stale non-zero count."""
    rows = (
        (
            await session.execute(
                select(StatsOrderModel.channel, func.count())
                .where(StatsOrderModel.outcome.is_(None))
                .group_by(StatsOrderModel.channel)
            )
        )
        .tuples()
        .all()
    )
    open_counts: dict[str, int] = dict(rows)
    for channel in Channel:
        metrics.orders_open.labels(channel=channel.value).set(open_counts.get(channel.value, 0))


class StatsProjector(Consumer):
    """Order events -> incrementally maintained statistics aggregates. See
    docs/api.md "Statistics"."""

    group_id = "stats"
    topics = ("forno.orders.v1", "forno.kitchen.v1")

    async def apply_event(
        self, session: AsyncSession, event_type: str, payload: dict[str, Any]
    ) -> None:
        if event_type == "OrderPlaced":
            # VAT rate does not travel on the wire event (only the domain
            # aggregate carries it), so this is gross-of-VAT-exclusive line
            # prices summed — a documented approximation for the dashboard,
            # not the authoritative receipt total (that lives on `orders`).
            total = sum(
                (Decimal(str(line["unit_price_snapshot"])) for line in payload["items"]),
                Decimal(0),
            )
            session.add(
                StatsOrderModel(
                    order_id=UUID(payload["order_id"]),
                    channel=payload["channel"],
                    outcome=None,
                    placed_at=datetime.fromisoformat(payload["occurred_at"]),
                    ready_at=None,
                    promised_at=None,
                    total_gross=total,
                )
            )
            await session.flush()
            await _refresh_open_orders_gauge(session)
            return

        row = await session.get(StatsOrderModel, UUID(payload["order_id"]))
        if row is None:
            return

        if event_type == "OrderAccepted":
            row.promised_at = datetime.fromisoformat(payload["promised_at"])
        elif event_type == "ItemStateChanged":
            if payload.get("status") == "READY" and row.first_item_ready_at is None:
                row.first_item_ready_at = datetime.fromisoformat(payload["occurred_at"])
        elif event_type == "OrderReady":
            _on_order_ready(row, datetime.fromisoformat(payload["occurred_at"]))
        elif event_type in _OUTCOME_BY_EVENT:
            outcome = _OUTCOME_BY_EVENT[event_type]
            row.outcome = outcome
            metrics.orders_total.labels(channel=row.channel, outcome=outcome).inc()
            await _refresh_open_orders_gauge(session)


def _on_order_ready(row: StatsOrderModel, ready_at: datetime) -> None:
    """Service-level observations for one order: time to ready, fire-together
    spread, and early/late against the promise."""
    row.ready_at = ready_at
    metrics.order_ready_seconds.labels(channel=row.channel).observe(
        (ready_at - row.placed_at).total_seconds()
    )
    if row.first_item_ready_at is not None:
        # Fire-together: how long the first pizza waited at the pass for
        # the last one. Per order, which in practice is per fire group —
        # the simulator orders every table as one course.
        metrics.fire_group_spread_seconds.observe(
            max((ready_at - row.first_item_ready_at).total_seconds(), 0.0)
        )
    if row.promised_at is not None:
        delta = (ready_at - row.promised_at).total_seconds()
        if delta > 0:
            metrics.promise_late_seconds.labels(channel=row.channel).observe(delta)
            metrics.promise_breaches_total.labels(channel=row.channel).inc()
        else:
            metrics.promise_early_seconds.labels(channel=row.channel).observe(-delta)
