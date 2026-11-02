"""Testcontainers: Postgres. See CONTRIBUTING.md "CI pipeline".

`make test-integration` runs this; `make test` does not — the fast gate
stays container-free. A fake producer stands in for Kafka: what's under
test here is the SQL polling/locking logic (`FOR UPDATE SKIP LOCKED`)
against a real database, not the Kafka wire protocol.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from forno.adapters.db.models import OutboxModel
from forno.adapters.messaging.outbox_relay import OutboxRelay

pytestmark = pytest.mark.integration

_NOW = datetime(2026, 7, 22, 18, 0, tzinfo=UTC)


class FakeProducer:
    def __init__(self) -> None:
        self.sent: list[tuple[str, bytes]] = []

    async def send_and_wait(self, topic: str, key: bytes, value: bytes, headers=None) -> None:
        del value, headers  # signature matches AIOKafkaProducer; the relay calls by keyword
        self.sent.append((topic, key))


async def _seed_outbox_row(session_factory, aggregate_id=None) -> int:
    async with session_factory() as session:
        row = OutboxModel(
            aggregate_id=aggregate_id or uuid4(),
            topic="forno.orders.v1",
            event_type="OrderPlaced",
            schema_version=1,
            payload={"hello": "world"},
            correlation_id="c-1",
            created_at=_NOW,
            sent_at=None,
        )
        session.add(row)
        await session.commit()
        return row.id


async def test_relay_publishes_and_marks_sent(session_factory):
    row_id = await _seed_outbox_row(session_factory)
    producer = FakeProducer()
    relay = OutboxRelay(session_factory, producer, poll_ms=200, batch_size=100)

    published = await relay._poll_once()

    assert published == 1
    assert len(producer.sent) == 1
    assert producer.sent[0][0] == "forno.orders.v1"

    async with session_factory() as session:
        row = await session.get(OutboxModel, row_id)
        assert row.sent_at is not None


async def test_relay_does_not_republish_already_sent_rows(session_factory):
    await _seed_outbox_row(session_factory)
    producer = FakeProducer()
    relay = OutboxRelay(session_factory, producer, poll_ms=200, batch_size=100)

    await relay._poll_once()
    published_again = await relay._poll_once()

    assert published_again == 0
    assert len(producer.sent) == 1


async def test_two_relay_replicas_claim_disjoint_batches(session_factory):
    """docs/adr/0002-transactional-outbox.md — horizontal scaling via
    `SKIP LOCKED`, no coordination. Two replicas polling concurrently must
    never both claim the same row."""
    row_ids = {await _seed_outbox_row(session_factory) for _ in range(10)}

    producer_a, producer_b = FakeProducer(), FakeProducer()
    relay_a = OutboxRelay(session_factory, producer_a, poll_ms=200, batch_size=5)
    relay_b = OutboxRelay(session_factory, producer_b, poll_ms=200, batch_size=5)

    await asyncio.gather(relay_a._poll_once(), relay_b._poll_once())

    keys_a = {key for _, key in producer_a.sent}
    keys_b = {key for _, key in producer_b.sent}
    assert keys_a.isdisjoint(keys_b)
    assert len(keys_a) + len(keys_b) == len(row_ids)

    async with session_factory() as session:
        from sqlalchemy import func, select

        (unsent_count,) = (
            await session.execute(
                select(func.count()).select_from(OutboxModel).where(OutboxModel.sent_at.is_(None))
            )
        ).one()
    assert unsent_count == 0
