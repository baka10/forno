from __future__ import annotations

import httpx
import pytest

from simulator.__main__ import _pick_free_table, run, time_scale_mismatch
from simulator.http_client import FornoClient
from simulator.scenario import TableConfig


def test_time_scale_mismatch_accepts_a_matching_server() -> None:
    assert time_scale_mismatch({"status": "ok", "time_scale": 60}, 60) is None


def test_time_scale_mismatch_names_both_values_and_the_fix() -> None:
    reason = time_scale_mismatch({"status": "ok", "time_scale": 1}, 60)
    assert reason is not None
    assert "time_scale=1" in reason
    assert "TIME_SCALE=60" in reason


def test_time_scale_mismatch_refuses_a_server_that_does_not_report_one() -> None:
    assert time_scale_mismatch({"status": "ok"}, 60) is not None


def test_pick_free_table_returns_smallest_fit() -> None:
    tables = [
        TableConfig(number=1, seats=2),
        TableConfig(number=2, seats=4),
        TableConfig(number=3, seats=6),
    ]

    assert _pick_free_table(tables, party_size=3, occupied=set()) == 2  # table 1 seats 2, too small


def test_pick_free_table_skips_occupied_tables() -> None:
    tables = [TableConfig(number=1, seats=4), TableConfig(number=2, seats=4)]

    assert _pick_free_table(tables, party_size=2, occupied={1}) == 2


def test_pick_free_table_returns_none_when_nothing_fits() -> None:
    tables = [TableConfig(number=1, seats=2)]

    assert _pick_free_table(tables, party_size=6, occupied=set()) is None


@pytest.mark.asyncio
async def test_run_bootstrap_only_stops_after_bootstrap(
    tiny_scenario_path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`make seed` runs the simulator with FORNO_SIM_BOOTSTRAP_ONLY=true — this
    should populate the pizzeria and exit immediately, never polling the
    kitchen or connecting to an SSE stream."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        calls.append(f"{request.method} {path}")
        if path == "/health/ready":
            return httpx.Response(200, json={"status": "ok"})
        if path == "/menu" and request.method == "GET":
            return httpx.Response(200, json=[{"id": "1"}, {"id": "2"}])
        if path == "/menu/items":
            return httpx.Response(201, json={"id": "menu-x"})
        return httpx.Response(201, json={})

    transport = httpx.MockTransport(handler)

    def fake_client(
        base_url: str, api_key: str, role: str, *, transport: httpx.AsyncBaseTransport = transport
    ) -> FornoClient:
        return FornoClient(base_url, api_key, role, transport=transport)

    monkeypatch.setattr("simulator.__main__.FornoClient", fake_client)

    exit_code = await run(
        tiny_scenario_path, "http://testserver", seed=1, duration_minutes=None, bootstrap_only=True
    )

    assert exit_code == 0
    assert any(call.startswith("POST /menu/items") for call in calls)
    assert any(call.startswith("POST /shifts") for call in calls)
    assert not any("/kitchen/tickets" in call for call in calls)
    assert not any("/streams/" in call for call in calls)
