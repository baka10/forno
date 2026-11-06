"""End to end through a real MCP client connected in-process, against the
fake API and Prometheus."""

from __future__ import annotations

import json
from typing import Any

import pytest
from mcp import Client

from forno_mcp.server import build_server

from .fakes import order

pytestmark = pytest.mark.asyncio


def data(result: Any) -> Any:  # noqa: ANN401
    assert not result.is_error, result.content
    sc = result.structured_content
    return sc.get("result", sc) if isinstance(sc, dict) else sc


@pytest.fixture
def busy(forno, prom):
    forno.orders = [
        order("o1", "IN_KITCHEN", placed_ago=1500, promise_in=-400),
        order("o2", "ACCEPTED", placed_ago=200, promise_in=300, items=[("Diavola", "QUEUED")]),
        order("o3", "READY", placed_ago=900, items=[("Funghi", "READY")]),
    ]
    prom.values = {
        "forno:promise_met:ratio_rate5m": 0.71,
        "max(forno_kitchen_oldest_ticket_age_seconds)": 1500,
        "sum(forno_oven_slots_occupied) / sum(forno_oven_slots_total)": 1.0,
        "max(forno_outbox_lag_seconds)": 0.4,
    }
    prom.alerts = [
        {
            "labels": {"alertname": "PromiseBudgetBurnFast", "severity": "page"},
            "annotations": {"summary": "Kitchen is falling behind"},
            "state": "firing",
            "activeAt": "2026-11-06T18:00:00Z",
        }
    ]
    return forno, prom


def server(settings, forno, prom):
    return build_server(settings, api_transport=forno.transport(), prom_transport=prom.transport())


async def test_tools_resources_and_prompts_are_listed(settings, forno, prom):
    async with Client(server(settings, forno, prom)) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
        assert {
            "get_service_snapshot",
            "diagnose",
            "watch_live",
            "explain_order",
            "start_simulation",
            "set_oven_active",
        } <= set(tools)
        assert tools["get_service_snapshot"].annotations.read_only_hint is True
        assert tools["set_oven_active"].annotations.destructive_hint is True
        uris = {str(r.uri) for r in (await client.list_resources()).resources}
        assert "forno://docs/runbook" in uris
        prompts = {p.name for p in (await client.list_prompts()).prompts}
        assert {"incident-triage", "why-is-the-kitchen-slow", "run-experiment"} <= prompts


async def test_snapshot_headline_tells_the_story(settings, busy):
    forno, prom = busy
    async with Client(server(settings, forno, prom)) as client:
        snap = data(await client.call_tool("get_service_snapshot", {}))
    assert snap["orders"]["open_total"] == 3
    assert snap["orders"]["already_late"] == 1
    assert snap["orders"]["latest_late_orders"][0]["order_id"] == "o1"
    headline = " ".join(snap["headline"])
    assert "14/14 slots busy" in headline
    assert "below the 95% SLO" in headline
    assert "PromiseBudgetBurnFast" in headline


async def test_diagnose_calls_it_capacity(settings, busy):
    forno, prom = busy
    async with Client(server(settings, forno, prom)) as client:
        diag = data(await client.call_tool("diagnose", {}))
    assert diag["category"] == "capacity"
    assert diag["runbook"]["heading"] == "PromiseBudgetBurn (Fast / Slow)"


async def test_snapshot_survives_prometheus_being_down(settings, busy):
    forno, prom = busy
    prom.down = True
    async with Client(server(settings, forno, prom)) as client:
        snap = data(await client.call_tool("get_service_snapshot", {}))
    assert "prometheus" in snap["unavailable_sources"]
    assert snap["orders"]["open_total"] == 3


async def test_api_down_is_reported_not_crashed(settings, forno, prom):
    forno.ready = False
    async with Client(server(settings, forno, prom)) as client:
        snap = data(await client.call_tool("get_service_snapshot", {}))
    assert snap["api"]["status"] == "not_ready"
    assert "API not ready" in snap["headline"][0]


async def test_explain_order(settings, busy):
    forno, prom = busy
    async with Client(server(settings, forno, prom)) as client:
        exp = data(await client.call_tool("explain_order", {"order_id": "o1"}))
        missing = await client.call_tool("explain_order", {"order_id": "nope"})
    assert exp["legal_next_states"] == ["READY", "CANCELLED", "VOIDED"]
    assert exp["can_still_be_cancelled"] is False  # an item is IN_OVEN
    assert "past promise" in exp["timing"]["outcome"]
    assert missing.is_error
    assert "list_orders" in missing.content[0].text


async def test_set_oven_active_uses_the_manager_role(settings, forno, prom):
    async with Client(server(settings, forno, prom)) as client:
        out = data(
            await client.call_tool(
                "set_oven_active", {"oven_id": "deck-2", "active": False, "reason": "test"}
            )
        )
    assert out["station"]["active"] is False
    method, path, headers = forno.requests[-1]
    assert (method, path, headers["x-role"]) == ("PATCH", "/stations/deck-2", "manager")


async def test_watch_live_summarises_sse(settings, forno, prom):
    forno.sse_events = {
        "kitchen": (
            "event: ticket.item.state_changed\n"
            'data: {"order_id": "o1", "status": "IN_OVEN", "oven": "deck-1"}\n\n'
            "event: ticket.item.state_changed\n"
            'data: {"order_id": "o1", "status": "READY"}\n\n'
        ),
        "floor": 'event: order.placed\ndata: {"order_id": "o9"}\n\n',
        "service": 'event: order.ready\ndata: {"order_id": "o1"}\n\n',
    }
    async with Client(server(settings, forno, prom)) as client:
        out = data(await client.call_tool("watch_live", {"seconds": 1}))
    assert out["events_total"] == 4
    assert out["item_transitions"] == {"IN_OVEN": 1, "READY": 1}
    assert "1 order(s) placed, 1 became ready" in out["story"][0]
    roles = {p: h["x-role"] for _, p, h in forno.requests if p.startswith("/streams/")}
    assert roles == {
        "/streams/kitchen": "chef",
        "/streams/service": "waiter",
        "/streams/floor": "manager",
    }


async def test_resources_give_the_agent_the_rules(settings, forno, prom):
    async with Client(server(settings, forno, prom)) as client:
        runbook = await client.read_resource("forno://docs/runbook")
        machines = await client.read_resource("forno://state-machines")
        adr = await client.read_resource("forno://adr/0002")
        scenario = await client.read_resource("forno://scenarios/oven-down")
    assert "Triage" in runbook.contents[0].text
    assert json.loads(machines.contents[0].text)["order_transitions"]["READY"]
    assert "outbox" in adr.contents[0].text.lower()
    assert "disable_oven" in scenario.contents[0].text


async def test_list_scenarios(settings, forno, prom):
    async with Client(server(settings, forno, prom)) as client:
        scenarios = data(await client.call_tool("list_scenarios", {}))
    names = {s["name"] for s in scenarios}
    assert "understaffed" in names
