"""One call that answers "what is going on in the pizzeria right now?".

Pulls the read side of the public API and, when the observability profile is
up, the same Prometheus signals the runbook's triage tree reads. Each source
is optional: a missing Prometheus degrades the snapshot, it does not fail it.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import httpx

from forno_mcp.clients import UnreachableError
from forno_mcp.knowledge import ORDER_OPEN

if TYPE_CHECKING:
    from collections.abc import Awaitable

    from forno_mcp.clients import FornoApi, Prometheus

# PromQL the runbook's triage tree and the alert rules are built on
# (deploy/prometheus/*.yml). Keys become snapshot fields.
PROM_SIGNALS: dict[str, str] = {
    "promise_met_ratio_5m": "forno:promise_met:ratio_rate5m",
    "promise_met_ratio_1h": "forno:promise_met:ratio_rate1h",
    "oldest_ticket_age_seconds": "max(forno_kitchen_oldest_ticket_age_seconds)",
    "kitchen_tickets_open": "sum(forno_kitchen_tickets_open)",
    "oven_utilisation": "sum(forno_oven_slots_occupied) / sum(forno_oven_slots_total)",
    "outbox_lag_seconds": "max(forno_outbox_lag_seconds)",
    "outbox_pending": "sum(forno_outbox_pending)",
    "projection_lag_seconds": "max(forno_projection_lag_seconds)",
    "consumer_lag_messages": "sum(forno_consumer_lag_messages)",
    "dlq_messages_15m": "sum(increase(forno_dlq_messages_total[15m]))",
    "remake_ratio_30m": (
        "sum(rate(forno_remakes_total[30m])) / sum(rate(forno_items_sold_total[30m]))"
    ),
    "api_5xx_ratio_5m": (
        'sum(rate(http_requests_total{job="forno-api", status="5xx"}[5m])) '
        '/ sum(rate(http_requests_total{job="forno-api"}[5m]))'
    ),
}

SLO_TARGET = 0.95  # docs/observability.md "SLO": 95% of orders ready by promised_at + grace


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


@dataclass
class Snapshot:
    taken_at: str
    api: dict[str, Any]
    orders: dict[str, Any] = field(default_factory=dict)
    kitchen: dict[str, Any] = field(default_factory=dict)
    ovens: list[dict[str, Any]] = field(default_factory=list)
    tables: dict[str, Any] = field(default_factory=dict)
    stats: dict[str, Any] = field(default_factory=dict)
    signals: dict[str, float | None] = field(default_factory=dict)
    alerts: list[dict[str, Any]] = field(default_factory=list)
    unavailable: list[str] = field(default_factory=list)
    headline: list[str] = field(default_factory=list)

    def public(self) -> dict[str, Any]:
        return {
            "taken_at": self.taken_at,
            "headline": self.headline,
            "api": self.api,
            "orders": self.orders,
            "kitchen": self.kitchen,
            "ovens": self.ovens,
            "tables": self.tables,
            "stats": self.stats,
            "signals": self.signals,
            "firing_alerts": self.alerts,
            "unavailable_sources": self.unavailable,
        }


async def _orders(api: FornoApi, now: datetime) -> dict[str, Any]:
    pages = await asyncio.gather(
        *(api.get_json("/orders", params={"status": s, "limit": 200}) for s in ORDER_OPEN)
    )
    by_status = {s: len(p) for s, p in zip(ORDER_OPEN, pages, strict=True)}
    waiting = [o for p in pages[:3] for o in p]  # PLACED, ACCEPTED, IN_KITCHEN: not ready yet
    late = []
    for order in waiting:
        promised = _parse_ts(order.get("promised_at"))
        if promised and promised < now:
            late.append(
                {
                    "order_id": order["id"],
                    "status": order["status"],
                    "table": order.get("table_number"),
                    "late_by_wall_seconds": round((now - promised).total_seconds(), 1),
                    "items": dict(Counter(i["status"] for i in order.get("items", []))),
                }
            )
    late.sort(key=lambda o: o["late_by_wall_seconds"], reverse=True)
    oldest = min(waiting, key=lambda o: o["placed_at"], default=None)
    return {
        "open_by_status": by_status,
        "open_total": sum(by_status.values()),
        "already_late": len(late),
        "latest_late_orders": late[:5],
        "oldest_unready": (
            {"order_id": oldest["id"], "status": oldest["status"], "placed_at": oldest["placed_at"]}
            if oldest
            else None
        ),
        "note": "Counts are capped at 200 per status (one page).",
    }


async def _kitchen(api: FornoApi) -> dict[str, Any]:
    tickets = await api.get_json("/kitchen/tickets", params={"status": "OPEN"})
    lines = [line for t in tickets for line in t.get("lines", [])]
    by_station: dict[str, Counter[str]] = {}
    for line in lines:
        by_station.setdefault(line["station"], Counter())[line["status"]] += 1
    return {
        "open_tickets": len(tickets),
        "items_by_station_and_status": {k: dict(v) for k, v in by_station.items()},
    }


async def _ovens(api: FornoApi) -> list[dict[str, Any]]:
    stations = await api.get_json("/stations")
    ovens = []
    for station in stations:
        if station.get("kind") != "oven":
            continue
        slots = int(station.get("slots") or 0)
        occupied = int(station.get("occupied") or 0)
        ovens.append(
            {
                "id": station.get("id"),
                "active": station.get("active", True),
                "occupied": occupied,
                "slots": slots,
                "utilisation": round(occupied / slots, 2) if slots else None,
                "bake_seconds": station.get("bake_seconds"),
            }
        )
    return ovens


async def _tables(api: FornoApi) -> dict[str, Any]:
    tables = await api.get_json("/tables")
    occupied = [t for t in tables if t.get("occupied")]
    return {"total": len(tables), "occupied": len(occupied)}


async def _signals(prom: Prometheus) -> dict[str, float | None]:
    values = await asyncio.gather(*(prom.scalar(expr) for expr in PROM_SIGNALS.values()))
    return {
        key: (round(v, 4) if v is not None else None)
        for key, v in zip(PROM_SIGNALS, values, strict=True)
    }


def _firing(alerts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "name": a.get("labels", {}).get("alertname"),
            "state": a.get("state"),
            "severity": a.get("labels", {}).get("severity"),
            "summary": a.get("annotations", {}).get("summary"),
            "since": a.get("activeAt"),
        }
        for a in alerts
        if a.get("state") in {"firing", "pending"}
    ]


def _headline(snap: Snapshot) -> list[str]:
    out: list[str] = []
    if snap.api.get("status") != "ok":
        out.append(f"API not ready: {snap.api.get('detail')}")
        return out
    if snap.orders:
        out.append(
            f"{snap.orders['open_total']} open orders "
            f"({snap.orders['open_by_status']}), {snap.orders['already_late']} already past "
            "their promised time."
        )
    if snap.ovens:
        active = [o for o in snap.ovens if o["active"]]
        used = sum(o["occupied"] for o in active)
        slots = sum(o["slots"] for o in active)
        down = [o["id"] for o in snap.ovens if not o["active"]]
        line = f"Ovens: {used}/{slots} slots busy across {len(active)} active oven(s)"
        out.append(line + (f"; out of service: {', '.join(map(str, down))}." if down else "."))
    met = snap.signals.get("promise_met_ratio_5m")
    if met is not None:
        verdict = "within" if met >= SLO_TARGET else "below"
        out.append(f"Promise SLI (5m): {met:.1%}, {verdict} the {SLO_TARGET:.0%} SLO.")
    if snap.alerts:
        names = ", ".join(sorted({str(a["name"]) for a in snap.alerts}))
        out.append(f"Alerts firing or pending: {names}.")
    elif "prometheus" not in snap.unavailable:
        out.append("No alerts firing.")
    return out


async def _ready(api: FornoApi) -> dict[str, Any]:
    resp = await api.request("GET", "/health/ready")
    if resp.status_code == httpx.codes.OK:
        body = resp.json()
        return {"status": "ok", "time_scale": body.get("time_scale"), "url": api.base_url}
    return {"status": "not_ready", "http_status": resp.status_code, "detail": resp.text[:200]}


async def _safe[T](coro: Awaitable[T], source: str, snap: Snapshot) -> T | None:
    try:
        return await coro
    except (UnreachableError, httpx.HTTPError, RuntimeError):
        snap.unavailable.append(source)
        return None


async def take_snapshot(api: FornoApi, prom: Prometheus) -> Snapshot:
    now = datetime.now(UTC)
    try:
        ready = await _ready(api)
    except UnreachableError as exc:
        ready = {"status": "unreachable", "detail": str(exc)}
    snap = Snapshot(taken_at=now.isoformat(timespec="seconds"), api=ready)
    if ready["status"] == "ok":
        orders, kitchen, ovens, tables, stats = await asyncio.gather(
            _safe(_orders(api, now), "orders", snap),
            _safe(_kitchen(api), "kitchen", snap),
            _safe(_ovens(api), "stations", snap),
            _safe(_tables(api), "tables", snap),
            _safe(api.get_json("/stats/summary"), "stats", snap),
        )
        snap.orders = orders or {}
        snap.kitchen = kitchen or {}
        snap.ovens = ovens or []
        snap.tables = tables or {}
        snap.stats = stats or {}
    signals, alerts = await asyncio.gather(
        _safe(_signals(prom), "prometheus", snap),
        _safe(prom.alerts(), "prometheus-alerts", snap),
    )
    snap.signals = signals or {}
    snap.alerts = _firing(alerts or [])
    snap.unavailable = sorted(set(snap.unavailable))
    if "prometheus-alerts" in snap.unavailable and "prometheus" in snap.unavailable:
        snap.unavailable.remove("prometheus-alerts")
    snap.headline = _headline(snap)
    return snap
