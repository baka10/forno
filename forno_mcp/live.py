"""Watch the SSE streams for a few seconds and say what happened.

A snapshot is a photo; this is a short video. It subscribes exactly as a KDS,
a waiter's handheld and a manager's floor view would (docs/api.md "SSE
streams"), so it also proves those streams carry what an operator needs.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections import Counter
from typing import TYPE_CHECKING, Any

import httpx
from httpx_sse import aconnect_sse

if TYPE_CHECKING:
    from forno_mcp.clients import FornoApi, Role

STREAM_ROLES: dict[str, Role] = {"kitchen": "chef", "service": "waiter", "floor": "manager"}
MAX_WATCH_SECONDS = 120
_SAMPLE_SIZE = 12


def _condense(stream: str, event: str, data: dict[str, Any]) -> dict[str, Any]:
    keep = ("order_id", "item_id", "status", "oven", "slot", "table_number", "occurred_at")
    out: dict[str, Any] = {"stream": stream, "event": event}
    out.update({k: data[k] for k in keep if k in data})
    return out


async def _consume(
    api: FornoApi, stream: str, events: list[dict[str, Any]], problems: list[str]
) -> None:
    role = STREAM_ROLES[stream]
    try:
        async with aconnect_sse(
            api.http,
            "GET",
            f"/streams/{stream}",
            headers=api.headers(role),
            timeout=httpx.Timeout(None, connect=5.0),
        ) as source:
            async for sse in source.aiter_sse():
                if sse.event == "stream.desync":
                    problems.append(f"{stream}: server dropped this subscriber (stream.desync)")
                    return
                try:
                    data = json.loads(sse.data) if sse.data else {}
                except json.JSONDecodeError:
                    data = {"raw": sse.data[:200]}
                events.append(_condense(stream, sse.event, data))
    except httpx.HTTPError as exc:
        problems.append(f"{stream}: {type(exc).__name__}: {exc}")


def summarise(events: list[dict[str, Any]], seconds: float) -> dict[str, Any]:
    by_type = Counter(e["event"] for e in events)
    statuses = Counter(
        e["status"] for e in events if e["event"] == "ticket.item.state_changed" and "status" in e
    )
    ovens = Counter(
        e["oven"] for e in events if e.get("status") == "IN_OVEN" and e.get("oven") is not None
    )
    story = [
        f"{by_type.get('order.placed', 0)} order(s) placed, "
        f"{by_type.get('order.ready', 0)} became ready, "
        f"{by_type.get('order.closed', 0)} closed.",
    ]
    if statuses:
        story.append(
            "Kitchen item transitions: "
            + ", ".join(f"{n}x {s}" for s, n in statuses.most_common())
            + "."
        )
    if ovens:
        story.append("Loaded into ovens: " + ", ".join(f"{o}: {n}" for o, n in ovens.items()) + ".")
    if not events:
        story = [f"No events in {seconds:.0f}s: the pizzeria is idle (or no simulation running)."]
    return {
        "watched_seconds": seconds,
        "events_total": len(events),
        "events_by_type": dict(by_type),
        "item_transitions": dict(statuses),
        "story": story,
        "latest_events": events[-_SAMPLE_SIZE:],
    }


async def watch(api: FornoApi, seconds: float, streams: list[str]) -> dict[str, Any]:
    unknown = [s for s in streams if s not in STREAM_ROLES]
    if unknown:
        msg = f"Unknown stream(s) {unknown}. Choose from: {', '.join(STREAM_ROLES)}."
        raise ValueError(msg)
    seconds = max(1.0, min(seconds, MAX_WATCH_SECONDS))
    events: list[dict[str, Any]] = []
    problems: list[str] = []
    tasks = [asyncio.create_task(_consume(api, s, events, problems)) for s in streams]
    await asyncio.wait(tasks, timeout=seconds)
    for task in tasks:
        task.cancel()
    for task in tasks:
        with contextlib.suppress(asyncio.CancelledError):
            await task
    result = summarise(events, seconds)
    result["problems"] = problems
    return result
