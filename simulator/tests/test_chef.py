from __future__ import annotations

import asyncio
from random import Random

import httpx
import pytest

from simulator.actors.chef import ChefActor
from simulator.catalog import ItemIndex
from simulator.clock import ScaledClock
from simulator.http_client import FornoClient
from simulator.shadow_ledger import ShadowLedger

_TICKET = {
    "id": "ticket-1",
    "order_id": "order-1",
    "status": "OPEN",
    "lines": [{"order_item_id": "item-1", "station": "prep", "status": "QUEUED"}],
}


@pytest.mark.asyncio
async def test_claims_a_queued_item_and_walks_it_to_ready() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        calls.append(f"{request.method} {path}")
        if path == "/kitchen/tickets":
            # Only ever hand out the ticket once, so the chef's own
            # in-flight `_claimed` tracking (not repeat polling) is what's
            # under test — the second poll returns nothing new.
            return httpx.Response(200, json=[_TICKET] if len(calls) <= 1 else [])
        return httpx.Response(200, json={})

    client = FornoClient("http://testserver", "dev", "chef", transport=httpx.MockTransport(handler))
    item_index = ItemIndex()
    chef = ChefActor(
        client, ShadowLedger(), item_index, remake_rate=0.0, clock=ScaledClock(1000), rng=Random(1)
    )

    task = asyncio.create_task(chef.run())
    await asyncio.sleep(1.0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    joined = " | ".join(calls)
    assert "POST /kitchen/tickets/ticket-1/items/item-1/start" in joined
    assert "POST /kitchen/tickets/ticket-1/items/item-1/oven-in" in joined
    assert "POST /kitchen/tickets/ticket-1/items/item-1/ready" in joined
    await client.aclose()


@pytest.mark.asyncio
async def test_preps_one_item_at_a_time_but_lets_pizzas_bake_in_parallel() -> None:
    """A chef has two hands: the second item's `start` must wait for the
    first item's prep, while the first item's bake runs in the background
    (its `ready` lands after the second `start`)."""
    two_lines = {
        "id": "ticket-1",
        "order_id": "order-1",
        "status": "OPEN",
        "lines": [
            {"order_item_id": "item-1", "station": "prep", "status": "QUEUED"},
            {"order_item_id": "item-2", "station": "prep", "status": "QUEUED"},
        ],
    }
    calls: list[str] = []
    polls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/kitchen/tickets":
            polls["n"] += 1
            return httpx.Response(200, json=[two_lines] if polls["n"] == 1 else [])
        calls.append(path.rsplit("/", 2)[-2] + ":" + path.rsplit("/", 1)[-1])
        return httpx.Response(200, json={})

    client = FornoClient("http://testserver", "dev", "chef", transport=httpx.MockTransport(handler))
    # Prep 120s and bake 480s at 1000x -> 0.12s and 0.48s wall.
    chef = ChefActor(
        client, ShadowLedger(), ItemIndex(), remake_rate=0.0, clock=ScaledClock(1000), rng=Random(1)
    )

    task = asyncio.create_task(chef.run())
    await asyncio.sleep(1.2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert calls.index("item-1:oven-in") < calls.index("item-2:start")  # hands busy during prep
    assert calls.index("item-2:start") < calls.index("item-1:ready")  # oven works unattended
    assert "item-2:ready" in calls
    await client.aclose()


@pytest.mark.asyncio
async def test_does_not_bake_an_item_the_oven_in_was_refused_for() -> None:
    """409 on oven-in means the order was cancelled underneath the chef —
    no background bake, no `ready`."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path.rsplit("/", 1)[-1])
        if request.url.path.endswith("/oven-in"):
            return httpx.Response(409, json={"detail": "cancelled"})
        return httpx.Response(200, json={})

    client = FornoClient("http://testserver", "dev", "chef", transport=httpx.MockTransport(handler))
    chef = ChefActor(
        client, ShadowLedger(), ItemIndex(), remake_rate=0.0, clock=ScaledClock(1000), rng=Random(1)
    )
    await chef._work_item("ticket-1", "item-1")
    await asyncio.sleep(0.6)

    assert "ready" not in calls
    await client.aclose()


@pytest.mark.asyncio
async def test_ignores_items_not_in_queued_status() -> None:
    non_queued_ticket = {
        "id": "ticket-1",
        "order_id": "order-1",
        "status": "OPEN",
        "lines": [{"order_item_id": "item-1", "station": "prep", "status": "PREPPING"}],
    }
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}")
        if request.url.path == "/kitchen/tickets":
            return httpx.Response(200, json=[non_queued_ticket])
        return httpx.Response(200, json={})

    client = FornoClient("http://testserver", "dev", "chef", transport=httpx.MockTransport(handler))
    chef = ChefActor(
        client, ShadowLedger(), ItemIndex(), remake_rate=0.0, clock=ScaledClock(1000), rng=Random(1)
    )

    task = asyncio.create_task(chef.run())
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert not any("start" in call for call in calls)
    await client.aclose()


@pytest.mark.asyncio
async def test_retries_oven_in_on_422_until_a_slot_frees() -> None:
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/kitchen/tickets":
            return httpx.Response(200, json=[])
        if path.endswith("/oven-in"):
            attempts["count"] += 1
            if attempts["count"] < 3:
                return httpx.Response(422, json={"detail": "no slot"})
            return httpx.Response(200, json={})
        return httpx.Response(200, json={})

    client = FornoClient("http://testserver", "dev", "chef", transport=httpx.MockTransport(handler))
    chef = ChefActor(
        client, ShadowLedger(), ItemIndex(), remake_rate=0.0, clock=ScaledClock(1000), rng=Random(1)
    )

    # ScaledClock(1000) shrinks these domain seconds to ~0.01s wall.
    in_oven = await chef._oven_in_with_retry(
        "ticket-1", "item-1", min_domain_seconds=10, max_domain_seconds=10
    )

    assert in_oven is True
    assert attempts["count"] == 3
    await client.aclose()


@pytest.mark.asyncio
async def test_scraps_instead_of_oven_in_when_remake_rate_is_one() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        calls.append(f"{request.method} {path}")
        if path == "/kitchen/tickets":
            return httpx.Response(200, json=[])
        return httpx.Response(200, json={})

    client = FornoClient("http://testserver", "dev", "chef", transport=httpx.MockTransport(handler))
    chef = ChefActor(
        client, ShadowLedger(), ItemIndex(), remake_rate=1.0, clock=ScaledClock(1000), rng=Random(1)
    )

    await chef._work_item("ticket-1", "item-1")

    assert any("scrap" in call for call in calls)
    assert not any("oven-in" in call for call in calls)
    await client.aclose()
