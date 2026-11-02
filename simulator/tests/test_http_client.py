from __future__ import annotations

import json
from typing import TYPE_CHECKING

import httpx
import pytest

from simulator.http_client import FornoClient

if TYPE_CHECKING:
    from collections.abc import Callable


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> FornoClient:
    return FornoClient(
        "http://testserver", "dev", "cashier", transport=httpx.MockTransport(handler)
    )


@pytest.mark.asyncio
async def test_post_sends_role_and_api_key_headers() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["headers"] = request.headers
        return httpx.Response(201, json={"id": "abc"})

    client = _client(handler)
    resp = await client.post("/orders", {"channel": "dine_in"})

    assert resp.status_code == 201
    assert seen["headers"]["x-api-key"] == "dev"
    assert seen["headers"]["x-role"] == "cashier"
    assert "x-request-id" in seen["headers"]
    await client.aclose()


@pytest.mark.asyncio
async def test_post_carries_idempotency_key_when_given() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["idem"] = request.headers.get("idempotency-key")
        return httpx.Response(201, json={})

    client = _client(handler)
    await client.post("/orders", {}, idempotency_key="key-123")

    assert seen["idem"] == "key-123"
    await client.aclose()


@pytest.mark.asyncio
async def test_post_does_not_raise_on_409() -> None:
    client = _client(lambda request: httpx.Response(409, json={"detail": "nope"}))
    resp = await client.post("/orders/x/cancel", {})

    assert resp.status_code == 409
    await client.aclose()


@pytest.mark.asyncio
async def test_post_retries_on_429_honouring_retry_after() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(201, json={})

    client = _client(handler)
    resp = await client.post("/orders", {})

    assert resp.status_code == 201
    assert len(calls) == 2
    await client.aclose()


@pytest.mark.asyncio
async def test_get_sends_params() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["query"] = str(request.url.params)
        return httpx.Response(200, json=[])

    client = _client(handler)
    await client.get("/kitchen/tickets", params={"station": "prep"})

    assert "station=prep" in seen["query"]
    await client.aclose()


@pytest.mark.asyncio
async def test_patch_sends_json_body() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={})

    client = _client(handler)
    await client.patch("/menu/items/x", {"price": "36.00"})

    assert seen["body"] == {"price": "36.00"}
    await client.aclose()


@pytest.mark.asyncio
async def test_stream_yields_parsed_sse_events() -> None:
    body = (
        b'id: 1\nevent: order.ready\ndata: {"order_id": "o1"}\n\n'
        b'id: 2\nevent: item.ready\ndata: {"item_id": "i1"}\n\n'
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)

    client = _client(handler)
    events = [event async for event in client.stream("/streams/service")]

    assert events == [
        {"event": "order.ready", "data": {"order_id": "o1"}, "id": "1"},
        {"event": "item.ready", "data": {"item_id": "i1"}, "id": "2"},
    ]
    await client.aclose()
