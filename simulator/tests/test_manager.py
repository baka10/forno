from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any

import httpx
import pytest

from simulator.actors.manager import ManagerActor
from simulator.catalog import MenuCatalog
from simulator.http_client import FornoClient
from simulator.scenario import Scenario
from simulator.shadow_ledger import ShadowLedger

if TYPE_CHECKING:
    from collections.abc import Callable


def _bootstrap_handler(
    menu_size: int,
) -> tuple[Callable[[httpx.Request], httpx.Response], list[httpx.Request]]:
    calls: list[httpx.Request] = []
    ready_polls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        path = request.url.path
        if path == "/menu/items" and request.method == "POST":
            body = json.loads(request.content)
            return httpx.Response(
                201, json={"id": f"menu-{body['sku']}", **body, "available": True}
            )
        if path == "/stations" and request.method == "POST":
            return httpx.Response(201, json={"kind": "oven"})
        if path == "/tables" and request.method == "POST":
            return httpx.Response(201, json={})
        if path == "/shifts" and request.method == "POST":
            return httpx.Response(201, json={})
        if path == "/health/ready":
            return httpx.Response(200, json={"status": "ok"})
        if path == "/menu":
            ready_polls["count"] += 1
            return httpx.Response(200, json=[{"id": f"menu-{i}"} for i in range(menu_size)])
        msg = f"unexpected request: {request.method} {path}"
        raise AssertionError(msg)

    return handler, calls


@pytest.mark.asyncio
async def test_bootstrap_creates_menu_stations_tables_shifts(tiny_scenario_path: str) -> None:
    scenario = Scenario.from_yaml(tiny_scenario_path)
    handler, calls = _bootstrap_handler(menu_size=len(scenario.pizzeria.menu))
    client = FornoClient(
        "http://testserver", "dev", "manager", transport=httpx.MockTransport(handler)
    )
    catalog = MenuCatalog()
    manager = ManagerActor(client, ShadowLedger(), scenario.pizzeria, catalog)

    await manager.bootstrap()

    posted_paths = [c.url.path for c in calls if c.method == "POST"]
    assert posted_paths.count("/menu/items") == len(scenario.pizzeria.menu)
    assert "/stations" in posted_paths
    assert "/tables" in posted_paths
    assert "/shifts" in posted_paths
    assert len(catalog.by_id) == len(scenario.pizzeria.menu)
    await client.aclose()


@pytest.mark.asyncio
async def test_bootstrap_treats_duplicate_menu_sku_as_already_registered(
    tiny_scenario_path: str,
) -> None:
    """Bootstrap re-runs on every simulator start (docs/configuration.md
    "Bootstrap flow"). A 409 on `POST /menu/items` means a prior run already
    created it — bootstrap must look the existing id up and continue, not
    crash on `raise_for_status()`."""
    scenario = Scenario.from_yaml(tiny_scenario_path)
    existing_menu = [
        {"id": f"existing-{item.sku}", "sku": item.sku} for item in scenario.pizzeria.menu
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/menu/items" and request.method == "POST":
            return httpx.Response(409, json={"detail": "duplicate sku"})
        if path == "/menu" and request.method == "GET":
            return httpx.Response(200, json=existing_menu)
        if path == "/stations" and request.method == "POST":
            return httpx.Response(201, json={"kind": "oven"})
        if path == "/tables" and request.method == "POST":
            return httpx.Response(201, json={})
        if path == "/shifts" and request.method == "POST":
            return httpx.Response(201, json={})
        if path == "/health/ready":
            return httpx.Response(200, json={"status": "ok"})
        msg = f"unexpected request: {request.method} {path}"
        raise AssertionError(msg)

    client = FornoClient(
        "http://testserver", "dev", "manager", transport=httpx.MockTransport(handler)
    )
    catalog = MenuCatalog()
    manager = ManagerActor(client, ShadowLedger(), scenario.pizzeria, catalog)

    await manager.bootstrap()  # must not raise

    assert len(catalog.by_id) == len(scenario.pizzeria.menu)
    assert set(catalog.by_id) == {m["id"] for m in existing_menu}
    await client.aclose()


@pytest.mark.asyncio
async def test_bootstrap_treats_duplicate_stations_and_tables_as_already_registered(
    tiny_scenario_path: str,
) -> None:
    """Ovens/stations/tables are keyed by a caller-chosen id (name/number),
    so a re-run collides on the primary key — the same "already configured,
    not an error" case as duplicate menu SKUs."""
    scenario = Scenario.from_yaml(tiny_scenario_path)
    handler, calls = _bootstrap_handler(menu_size=len(scenario.pizzeria.menu))

    def conflicting_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path in ("/stations", "/tables") and request.method == "POST":
            calls.append(request)
            return httpx.Response(409, json={"detail": "already exists"})
        return handler(request)

    client = FornoClient(
        "http://testserver", "dev", "manager", transport=httpx.MockTransport(conflicting_handler)
    )
    catalog = MenuCatalog()
    manager = ManagerActor(client, ShadowLedger(), scenario.pizzeria, catalog)

    await manager.bootstrap()  # must not raise

    posted_paths = [c.url.path for c in calls if c.method == "POST"]
    assert "/stations" in posted_paths
    assert "/tables" in posted_paths
    await client.aclose()


@pytest.mark.asyncio
async def test_apply_menu_change_patches_the_right_item(tiny_scenario_path: str) -> None:
    scenario = Scenario.from_yaml(tiny_scenario_path)
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/menu/items/"):
            seen["path"] = request.url.path
            seen["body"] = json.loads(request.content)
            return httpx.Response(200, json={})
        raise AssertionError(request.url.path)

    client = FornoClient(
        "http://testserver", "dev", "manager", transport=httpx.MockTransport(handler)
    )
    catalog = MenuCatalog()
    catalog.register("menu-margherita", scenario.pizzeria.menu[0])
    manager = ManagerActor(client, ShadowLedger(), scenario.pizzeria, catalog)

    await manager.apply_menu_change({"sku": "margherita", "price": "36.00"})

    assert seen["path"] == "/menu/items/menu-margherita"
    assert seen["body"] == {"price": "36.00"}
    await client.aclose()


@pytest.mark.asyncio
async def test_apply_fault_takes_the_oven_out_of_service_via_the_api(
    tiny_scenario_path: str,
) -> None:
    scenario = Scenario.from_yaml(tiny_scenario_path)
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": "deck-2", "active": False})

    client = FornoClient(
        "http://testserver", "dev", "manager", transport=httpx.MockTransport(handler)
    )
    manager = ManagerActor(client, ShadowLedger(), scenario.pizzeria, MenuCatalog())

    await manager.apply_fault({"at_minute": 40, "action": "disable_oven", "oven": "deck-2"})

    assert (seen["method"], seen["path"], seen["body"]) == (
        "PATCH",
        "/stations/deck-2",
        {"active": False},
    )
    await client.aclose()


@pytest.mark.asyncio
async def test_watch_floor_stream_reconnects_after_a_dropped_connection(
    tiny_scenario_path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("simulator.actors.manager._RECONNECT_DELAY_SECONDS", 0.01)
    scenario = Scenario.from_yaml(tiny_scenario_path)
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/streams/floor"
        calls["count"] += 1
        if calls["count"] == 1:
            msg = "connection dropped"
            raise httpx.ReadError(msg, request=request)
        sse_body = b'id: 1\nevent: table.seated\ndata: {"table": 1}\n\n'
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=sse_body)

    client = FornoClient(
        "http://testserver", "dev", "manager", transport=httpx.MockTransport(handler)
    )
    manager = ManagerActor(client, ShadowLedger(), scenario.pizzeria, MenuCatalog())

    task = asyncio.create_task(manager.watch_floor_stream())
    await asyncio.sleep(0.2)  # past the patched reconnect delay so a retry happens
    still_running = not task.done()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert still_running
    assert calls["count"] >= 2
    await client.aclose()


@pytest.mark.asyncio
async def test_void_order_posts_to_the_void_endpoint(tiny_scenario_path: str) -> None:
    scenario = Scenario.from_yaml(tiny_scenario_path)
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        return httpx.Response(200, json={})

    client = FornoClient(
        "http://testserver", "dev", "manager", transport=httpx.MockTransport(handler)
    )
    manager = ManagerActor(client, ShadowLedger(), scenario.pizzeria, MenuCatalog())

    await manager.void_order("order-1", "walkout")

    assert seen["path"] == "/orders/order-1/void"
    await client.aclose()
