"""Thin async clients for the three things an operator can see: the API
(as any role), the SSE streams, and Prometheus.

Every failure is turned into an error that tells the agent what to do next
("is the stack up?"), not a stack trace — the agent is the operator here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

import httpx

if TYPE_CHECKING:
    from forno_mcp.config import Settings

Role = Literal["manager", "cashier", "chef", "waiter"]


class UnreachableError(RuntimeError):
    """A dependency the agent relies on is not answering."""


class FornoApi:
    def __init__(
        self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._settings = settings
        self._client = httpx.AsyncClient(
            base_url=settings.api_url,
            timeout=settings.http_timeout_seconds,
            transport=transport,
        )

    @property
    def base_url(self) -> str:
        return self._settings.api_url

    def headers(self, role: Role) -> dict[str, str]:
        return {"X-API-Key": self._settings.api_key, "X-Role": role, "X-Request-ID": "mcp"}

    async def request(
        self,
        method: str,
        path: str,
        *,
        role: Role = "manager",
        params: dict[str, Any] | None = None,
        json: Any = None,  # noqa: ANN401 -- any JSON body, passed through to httpx
    ) -> httpx.Response:
        try:
            return await self._client.request(
                method, path, headers=self.headers(role), params=params, json=json
            )
        except httpx.TransportError as exc:
            msg = (
                f"Forno API at {self._settings.api_url} is not reachable ({type(exc).__name__}). "
                "Is the stack running? Start it with `make up`."
            )
            raise UnreachableError(msg) from exc

    async def get_json(
        self, path: str, *, role: Role = "manager", params: dict[str, Any] | None = None
    ) -> Any:  # noqa: ANN401 -- decoded JSON; callers know the endpoint's shape
        resp = await self.request("GET", path, role=role, params=params)
        resp.raise_for_status()
        return resp.json()

    @property
    def http(self) -> httpx.AsyncClient:
        return self._client

    async def aclose(self) -> None:
        await self._client.aclose()


class Prometheus:
    def __init__(
        self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._url = settings.prometheus_url
        self._client = httpx.AsyncClient(
            base_url=settings.prometheus_url,
            timeout=settings.http_timeout_seconds,
            transport=transport,
        )

    async def _get(self, path: str, params: dict[str, str] | None = None) -> Any:  # noqa: ANN401 -- Prometheus `data` payload, shape depends on the endpoint
        try:
            resp = await self._client.get(path, params=params)
        except httpx.TransportError as exc:
            msg = (
                f"Prometheus at {self._url} is not reachable. Metrics-based checks need the "
                "observability profile: `make up` starts it."
            )
            raise UnreachableError(msg) from exc
        resp.raise_for_status()
        body = resp.json()
        if body.get("status") != "success":
            msg = f"Prometheus error: {body.get('error', body)}"
            raise RuntimeError(msg)
        return body["data"]

    async def scalar(self, expr: str) -> float | None:
        """Sum of an instant vector, or None when the query has no data yet
        (an idle window is "no data", not zero — see recording_rules.yml)."""
        data = await self._get("/api/v1/query", {"query": expr})
        values = [float(sample["value"][1]) for sample in data.get("result", [])]
        values = [v for v in values if v == v]  # drop NaN  # noqa: PLR0124
        return sum(values) if values else None

    async def vector(self, expr: str) -> list[tuple[dict[str, str], float]]:
        data = await self._get("/api/v1/query", {"query": expr})
        return [(s["metric"], float(s["value"][1])) for s in data.get("result", [])]

    async def alerts(self) -> list[dict[str, Any]]:
        data = await self._get("/api/v1/alerts")
        alerts: list[dict[str, Any]] = data.get("alerts", [])
        return alerts

    async def aclose(self) -> None:
        await self._client.aclose()
