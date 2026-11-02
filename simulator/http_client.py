"""Thin HTTP client wrapper. Every mutating call carries `Idempotency-Key`
and `X-Request-ID`; retries on `429` honour `Retry-After`. See
docs/api.md "Conventions" and "Idempotency".
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import httpx
from httpx_sse import aconnect_sse

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

_MAX_RETRIES = 5
_DEFAULT_RETRY_AFTER_SECONDS = 1.0
# Connect/write/pool stay at 5s; read is unbounded — the server's 15s
# heartbeat is what detects a dead peer, not a client-side read timeout.
_STREAM_TIMEOUT = httpx.Timeout(5.0, read=None)


class FornoClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        role: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, transport=transport)
        self._api_key = api_key
        self._role = role

    def _headers(self) -> dict[str, str]:
        return {
            "X-API-Key": self._api_key,
            "X-Role": self._role,
            "X-Request-ID": str(uuid4()),
        }

    async def _with_429_retry(self, send: Callable[[dict[str, str]], Any]) -> httpx.Response:
        response: httpx.Response | None = None
        for _attempt in range(_MAX_RETRIES):
            response = await send(self._headers())
            if response.status_code != httpx.codes.TOO_MANY_REQUESTS:
                return response
            delay = float(response.headers.get("Retry-After", _DEFAULT_RETRY_AFTER_SECONDS))
            await asyncio.sleep(delay)
        assert response is not None  # noqa: S101 -- type-narrowing only; loop always runs >= 1 time
        return response

    async def post(
        self, path: str, json: dict[str, Any], idempotency_key: str | None = None
    ) -> httpx.Response:
        """Retries on 429 with `Retry-After`. Raises nothing on 409/422 — the
        caller (an actor) is expected to branch on those, exactly as a real
        client would. See docs/api.md "Errors"."""

        async def send(headers: dict[str, str]) -> httpx.Response:
            if idempotency_key is not None:
                headers = {**headers, "Idempotency-Key": idempotency_key}
            return await self._client.post(path, json=json, headers=headers)

        return await self._with_429_retry(send)

    async def get(self, path: str, params: dict[str, Any] | None = None) -> httpx.Response:
        async def send(headers: dict[str, str]) -> httpx.Response:
            return await self._client.get(path, params=params, headers=headers)

        return await self._with_429_retry(send)

    async def patch(self, path: str, json: dict[str, Any]) -> httpx.Response:
        async def send(headers: dict[str, str]) -> httpx.Response:
            return await self._client.patch(path, json=json, headers=headers)

        return await self._with_429_retry(send)

    async def stream(
        self, path: str, last_event_id: str | None = None
    ) -> AsyncIterator[dict[str, Any]]:
        """SSE stream with `Last-Event-ID` reconnect support. See
        docs/api.md "SSE streams"."""
        headers = self._headers()
        if last_event_id is not None:
            headers["Last-Event-ID"] = last_event_id
        # A long-lived stream must not inherit the client's default 5s read
        # timeout: the server heartbeats every 15s, so the default tore the
        # connection down on every quiet stretch, and anything emitted during
        # the reconnect gap was lost.
        async with aconnect_sse(
            self._client, "GET", path, headers=headers, timeout=_STREAM_TIMEOUT
        ) as source:
            async for sse in source.aiter_sse():
                yield {
                    "event": sse.event,
                    "data": json.loads(sse.data) if sse.data else {},
                    "id": sse.id or None,
                }

    async def aclose(self) -> None:
        await self._client.aclose()
