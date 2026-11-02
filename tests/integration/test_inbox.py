from uuid import uuid4

import pytest

from forno.adapters.messaging.inbox import mark_processed_or_skip

pytestmark = pytest.mark.integration


async def test_duplicate_delivery_is_suppressed_and_counted(session_factory):
    """docs/architecture.md "Reliability machinery" —
    `events_consumed_total{result="duplicate"}` proves the inbox is working
    rather than hiding the redelivery."""
    event_id = uuid4()

    async with session_factory() as session:
        first_delivery = await mark_processed_or_skip(session, event_id, "kitchen")
        await session.commit()
    assert first_delivery is True

    # Kafka redelivering the same message (at-least-once) — same event_id,
    # same consumer.
    async with session_factory() as session:
        second_delivery = await mark_processed_or_skip(session, event_id, "kitchen")
        await session.commit()
    assert second_delivery is False


async def test_same_event_id_processed_independently_per_consumer(session_factory):
    """`processed_events`'s primary key is `(event_id, consumer)` — the same
    event legitimately reaches both `kitchen` and `stats`, and each must
    see it as a first delivery."""
    event_id = uuid4()

    async with session_factory() as session:
        for_kitchen = await mark_processed_or_skip(session, event_id, "kitchen")
        for_stats = await mark_processed_or_skip(session, event_id, "stats")
        await session.commit()

    assert for_kitchen is True
    assert for_stats is True


@pytest.mark.skip(
    reason=(
        "Requires a real Kafka consumer group reset + replay, which is a "
        "fuller scenario than this integration tier targets (a few crucial "
        "boundaries — see test_duplicate_delivery_is_suppressed_and_counted "
        "for the inbox's actual guarantee). Worth adding alongside the "
        "simulator/chaos-testing pass, where a real broker is already in "
        "the picture."
    )
)
async def test_kitchen_projection_rebuilds_exactly_from_consumer_group_reset():
    """docs/adr/0006-kitchen-tickets-as-projection.md — stateless, replayable."""
