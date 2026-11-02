from __future__ import annotations

import asyncio
from random import Random
from typing import TYPE_CHECKING

import httpx
import pytest

if TYPE_CHECKING:
    from collections.abc import Callable

from simulator.actors.waiter import WaiterActor
from simulator.clock import ScaledClock
from simulator.http_client import FornoClient
from simulator.shadow_ledger import ShadowLedger

_ORDER_READY = b'id: 1\nevent: order.ready\ndata: {"order_id": "order-1"}\n\n'
_ORDER_BODY = {
    "id": "order-1",
    "status": "READY",
    "items": [
        {"id": "item-1", "status": "READY"},
        {"id": "item-2", "status": "READY"},
        {"id": "item-0", "status": "SCRAPPED"},
    ],
}


def _handler_delivering_into(
    delivered: list[str], sse_body: bytes = _ORDER_READY
) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/streams/service":
            return httpx.Response(
                200, headers={"content-type": "text/event-stream"}, content=sse_body
            )
        if request.url.path == "/orders":
            return httpx.Response(200, json=[])  # nothing to recover on connect
        if request.url.path == "/orders/order-1":
            return httpx.Response(200, json=_ORDER_BODY)
        if request.url.path.endswith("/delivered"):
            delivered.append(request.url.path)
            return httpx.Response(200, json={})
        raise AssertionError(request.url.path)

    return handler


@pytest.mark.asyncio
async def test_delivers_every_ready_item_of_the_order_on_order_ready_event() -> None:
    """Fire-together: the waiter carries the whole order once it is READY,
    reading the item list from the API (the read model), not from events."""
    delivered: list[str] = []
    client = FornoClient(
        "http://testserver",
        "dev",
        "waiter",
        transport=httpx.MockTransport(_handler_delivering_into(delivered)),
    )
    waiter = WaiterActor(client, ShadowLedger(), ScaledClock(1000), Random(1))

    task = asyncio.create_task(waiter.run())
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert delivered == [
        "/orders/order-1/items/item-1/delivered",
        "/orders/order-1/items/item-2/delivered",
    ]
    await client.aclose()


@pytest.mark.asyncio
async def test_dedups_repeated_order_ready_events_for_the_same_order() -> None:
    # A reconnect can replay the same frame; the waiter must walk once.
    delivered: list[str] = []
    client = FornoClient(
        "http://testserver",
        "dev",
        "waiter",
        transport=httpx.MockTransport(_handler_delivering_into(delivered, _ORDER_READY * 2)),
    )
    waiter = WaiterActor(client, ShadowLedger(), ScaledClock(1000), Random(1))

    task = asyncio.create_task(waiter.run())
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert delivered == [
        "/orders/order-1/items/item-1/delivered",
        "/orders/order-1/items/item-2/delivered",
    ]
    await client.aclose()


@pytest.mark.asyncio
async def test_two_waiters_sharing_a_floor_carry_each_order_once() -> None:
    """Both waiters hear the same `order.ready`; the shared claim set means
    exactly one walks it to the table (the ledger would otherwise count the
    second, idempotent delivery as a duplicate)."""
    delivered: list[str] = []
    transport = httpx.MockTransport(_handler_delivering_into(delivered))
    client = FornoClient("http://testserver", "dev", "waiter", transport=transport)
    floor: set[str] = set()
    waiters = [
        WaiterActor(client, ShadowLedger(), ScaledClock(1000), Random(i), floor) for i in range(2)
    ]

    tasks = [asyncio.create_task(w.run()) for w in waiters]
    await asyncio.sleep(0.05)
    for task in tasks:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert delivered == [
        "/orders/order-1/items/item-1/delivered",
        "/orders/order-1/items/item-2/delivered",
    ]
    await client.aclose()


@pytest.mark.asyncio
async def test_recovers_orders_that_went_ready_while_disconnected() -> None:
    """The stream has no replay: on (re)connect the waiter sweeps
    `GET /orders?status=READY` and delivers what it missed."""
    delivered: list[str] = []
    empty_stream = b""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/streams/service":
            return httpx.Response(
                200, headers={"content-type": "text/event-stream"}, content=empty_stream
            )
        if request.url.path == "/orders":
            assert request.url.params["status"] == "READY"
            return httpx.Response(200, json=[_ORDER_BODY])
        if request.url.path == "/orders/order-1":
            return httpx.Response(200, json=_ORDER_BODY)
        if request.url.path.endswith("/delivered"):
            delivered.append(request.url.path)
            return httpx.Response(200, json={})
        raise AssertionError(request.url.path)

    client = FornoClient(
        "http://testserver", "dev", "waiter", transport=httpx.MockTransport(handler)
    )
    waiter = WaiterActor(client, ShadowLedger(), ScaledClock(1000), Random(1))

    task = asyncio.create_task(waiter.run())
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert delivered == [
        "/orders/order-1/items/item-1/delivered",
        "/orders/order-1/items/item-2/delivered",
    ]
    await client.aclose()


@pytest.mark.asyncio
async def test_ignores_item_ready_events_the_order_is_not_ready_yet() -> None:
    """`item.ready` is per item; delivery is refused (409) until the whole
    order is READY, so the waiter does not even try."""
    sse_body = b'id: 1\nevent: item.ready\ndata: {"order_id": "order-1", "item_id": "item-1"}\n\n'
    posted = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/streams/service":
            return httpx.Response(
                200, headers={"content-type": "text/event-stream"}, content=sse_body
            )
        if request.url.path == "/orders":
            return httpx.Response(200, json=[])  # the on-connect sweep finds nothing
        posted.append(request.url.path)
        return httpx.Response(200, json={})

    client = FornoClient(
        "http://testserver", "dev", "waiter", transport=httpx.MockTransport(handler)
    )
    waiter = WaiterActor(client, ShadowLedger(), ScaledClock(1000), Random(1))

    task = asyncio.create_task(waiter.run())
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert posted == []
    await client.aclose()
