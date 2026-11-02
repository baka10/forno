"""`ServerSentEvent(**data)` regexes on `id` (sse_starlette) — it must be a
string. `SseBroadcaster` used to hand out the bare int counter directly,
which crashed every live stream with `TypeError: expected string or
bytes-like object, got 'int'` the moment real traffic exercised it.
"""

from __future__ import annotations

from forno.adapters.messaging.sse_fanout import SseBroadcaster


async def test_published_envelope_id_is_a_string() -> None:
    broadcaster = SseBroadcaster(queue_size=10)
    queue = broadcaster.subscribe("floor")

    await broadcaster.publish("floor", {"event": "order.placed", "data": "{}"})

    envelope = queue.get_nowait()
    assert isinstance(envelope["id"], str)
    assert envelope["id"] == "1"


async def test_published_ids_are_monotonically_increasing_strings() -> None:
    broadcaster = SseBroadcaster(queue_size=10)
    queue = broadcaster.subscribe("floor")

    await broadcaster.publish("floor", {"event": "a", "data": "{}"})
    await broadcaster.publish("floor", {"event": "b", "data": "{}"})

    assert queue.get_nowait()["id"] == "1"
    assert queue.get_nowait()["id"] == "2"


async def test_overflow_desync_event_id_is_a_string() -> None:
    broadcaster = SseBroadcaster(queue_size=1)
    queue = broadcaster.subscribe("floor")
    await broadcaster.publish("floor", {"event": "a", "data": "{}"})  # fills the queue

    await broadcaster.publish("floor", {"event": "b", "data": "{}"})  # overflow -> desync

    envelope = queue.get_nowait()
    assert envelope["event"] == "stream.desync"
    assert isinstance(envelope["id"], str)
