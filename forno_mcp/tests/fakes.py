"""A small fake of the public Forno API and Prometheus, shaped like
src/forno/contracts/responses.py and the Prometheus HTTP API."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

NOW = datetime.now(UTC)


def iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def order(  # noqa: PLR0913 -- a fixture builder, keywords only
    oid: str,
    status: str,
    *,
    placed_ago: float = 300,
    promise_in: float = -60,
    items: list[tuple[str, str]] | None = None,
    table: int | None = 4,
) -> dict[str, Any]:
    items = items or [("Margherita", "IN_OVEN")]
    return {
        "id": oid,
        "status": status,
        "channel": "dine_in",
        "table_number": table,
        "placed_at": iso(NOW - timedelta(seconds=placed_ago)),
        "promised_at": iso(NOW + timedelta(seconds=promise_in)),
        "ready_at": None,
        "total_gross": "70.00",
        "currency": "PLN",
        "items": [
            {
                "id": f"{oid}-i{n}",
                "menu_item_id": "m1",
                "name": name,
                "unit_price": "35.00",
                "status": st,
                "fire_group": 1,
                "remake_of": None,
            }
            for n, (name, st) in enumerate(items)
        ],
    }


@dataclass
class FakeForno:
    ready: bool = True
    time_scale: int = 1
    orders: list[dict[str, Any]] = field(default_factory=list)
    ovens: list[dict[str, Any]] = field(
        default_factory=lambda: [
            {
                "id": "deck-1",
                "kind": "oven",
                "slots": 8,
                "occupied": 8,
                "bake_seconds": 480,
                "active": True,
            },
            {
                "id": "deck-2",
                "kind": "oven",
                "slots": 6,
                "occupied": 6,
                "bake_seconds": 480,
                "active": True,
            },
        ]
    )
    sse_events: dict[str, str] = field(default_factory=dict)
    requests: list[tuple[str, str, dict[str, str]]] = field(default_factory=list)

    def handler(self, request: httpx.Request) -> httpx.Response:  # noqa: C901, PLR0911
        path = request.url.path
        self.requests.append((request.method, path, dict(request.headers)))
        if path == "/health/ready":
            if not self.ready:
                return httpx.Response(503, json={"detail": "Database not reachable"})
            return httpx.Response(200, json={"status": "ok", "time_scale": self.time_scale})
        if path == "/orders":
            status = request.url.params.get("status")
            rows = [o for o in self.orders if status in (None, o["status"])]
            return httpx.Response(200, json=rows)
        if path.startswith("/orders/"):
            oid = path.rsplit("/", 1)[1]
            match = next((o for o in self.orders if o["id"] == oid), None)
            return httpx.Response(200, json=match) if match else httpx.Response(404, json={})
        if path == "/kitchen/tickets":
            tickets = [
                {
                    "id": f"t-{o['id']}",
                    "order_id": o["id"],
                    "status": "OPEN",
                    "lines": [
                        {"order_item_id": i["id"], "station": "oven", "status": i["status"]}
                        for i in o["items"]
                    ],
                }
                for o in self.orders
                if o["status"] in {"ACCEPTED", "IN_KITCHEN"}
            ]
            return httpx.Response(200, json=tickets)
        if path == "/stations" and request.method == "GET":
            return httpx.Response(200, json=self.ovens)
        if path.startswith("/stations/") and request.method == "PATCH":
            sid = path.rsplit("/", 1)[1]
            oven = next((o for o in self.ovens if o["id"] == sid), None)
            if oven is None:
                return httpx.Response(404, json={})
            oven["active"] = json.loads(request.content)["active"]
            return httpx.Response(200, json=oven)
        if path == "/tables":
            return httpx.Response(200, json=[{"number": 1, "seats": 4, "occupied": True}])
        if path == "/stats/summary":
            return httpx.Response(
                200,
                json={
                    "covers": 40,
                    "revenue": "1400.00",
                    "median_ready_seconds": 900.0,
                    "breach_rate": 0.3,
                    "remake_rate": 0.02,
                },
            )
        if path.startswith("/streams/"):
            body = self.sse_events.get(path.rsplit("/", 1)[1], "")
            return httpx.Response(
                200, headers={"content-type": "text/event-stream"}, content=body.encode()
            )
        return httpx.Response(404, json={"detail": f"fake has no {path}"})

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


@dataclass
class FakePrometheus:
    values: dict[str, float] = field(default_factory=dict)
    alerts: list[dict[str, Any]] = field(default_factory=list)
    down: bool = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            msg = "connection refused"
            raise httpx.ConnectError(msg, request=request)
        if request.url.path == "/api/v1/alerts":
            return httpx.Response(200, json={"status": "success", "data": {"alerts": self.alerts}})
        query = request.url.params["query"]
        result = (
            [{"metric": {}, "value": [0, str(self.values[query])]}] if query in self.values else []
        )
        return httpx.Response(
            200, json={"status": "success", "data": {"resultType": "vector", "result": result}}
        )

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)
