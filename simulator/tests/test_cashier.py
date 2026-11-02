from __future__ import annotations

from random import Random
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from simulator.actors.cashier import CashierActor
from simulator.catalog import ItemIndex, MenuCatalog
from simulator.http_client import FornoClient
from simulator.scenario import Scenario
from simulator.shadow_ledger import ShadowLedger


def _order_response(order_id: str = "order-1") -> dict[str, Any]:
    return {
        "id": order_id,
        "status": "ACCEPTED",
        "promised_at": "2026-01-01T18:30:00+00:00",
        "items": [
            {"id": "item-1", "menu_item_id": "menu-margherita"},
            {"id": "item-2", "menu_item_id": "menu-margherita"},
        ],
    }


@pytest.mark.asyncio
async def test_place_order_returns_body_on_201(tiny_scenario_path: str) -> None:
    scenario = Scenario.from_yaml(tiny_scenario_path)
    catalog = MenuCatalog()
    catalog.register("menu-margherita", scenario.pizzeria.menu[0])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json=_order_response())

    client = FornoClient(
        "http://testserver", "dev", "cashier", transport=httpx.MockTransport(handler)
    )
    item_index = ItemIndex()
    manager = AsyncMock()
    cashier = CashierActor(client, manager, ShadowLedger(), item_index, catalog, 0.0, Random(1))

    order = await cashier.place_order(
        "dine_in", 1, [{"menu_item_id": "menu-margherita", "quantity": 2, "fire_group": 1}]
    )

    assert order is not None
    assert order["id"] == "order-1"
    assert item_index.lookup("item-1") is not None
    await client.aclose()


@pytest.mark.asyncio
async def test_place_order_returns_none_on_rejection(tiny_scenario_path: str) -> None:
    client = FornoClient(
        "http://testserver",
        "dev",
        "cashier",
        transport=httpx.MockTransport(
            lambda r: httpx.Response(422, json={"detail": "unavailable"})
        ),
    )
    manager = AsyncMock()
    cashier = CashierActor(
        client, manager, ShadowLedger(), ItemIndex(), MenuCatalog(), 0.0, Random(1)
    )

    order = await cashier.place_order("dine_in", 1, [])

    assert order is None
    await client.aclose()


@pytest.mark.asyncio
async def test_place_order_double_submits_when_rate_is_one(tiny_scenario_path: str) -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.headers.get("idempotency-key"))
        return httpx.Response(201, json=_order_response())

    client = FornoClient(
        "http://testserver", "dev", "cashier", transport=httpx.MockTransport(handler)
    )
    manager = AsyncMock()
    cashier = CashierActor(
        client, manager, ShadowLedger(), ItemIndex(), MenuCatalog(), 1.0, Random(1)
    )

    await cashier.place_order("dine_in", 1, [])

    assert len(calls) == 2
    assert calls[0] == calls[1]  # same Idempotency-Key both times
    await client.aclose()


@pytest.mark.asyncio
async def test_cancel_or_escalate_calls_manager_void_on_409() -> None:
    client = FornoClient(
        "http://testserver",
        "dev",
        "cashier",
        transport=httpx.MockTransport(lambda r: httpx.Response(409, json={"detail": "in oven"})),
    )
    manager = AsyncMock()
    cashier = CashierActor(
        client, manager, ShadowLedger(), ItemIndex(), MenuCatalog(), 0.0, Random(1)
    )

    await cashier.cancel_or_escalate("order-1")

    manager.void_order.assert_awaited_once_with("order-1", "walkout")
    await client.aclose()


@pytest.mark.asyncio
async def test_cancel_or_escalate_does_not_call_manager_on_success() -> None:
    client = FornoClient(
        "http://testserver",
        "dev",
        "cashier",
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})),
    )
    manager = AsyncMock()
    cashier = CashierActor(
        client, manager, ShadowLedger(), ItemIndex(), MenuCatalog(), 0.0, Random(1)
    )

    await cashier.cancel_or_escalate("order-1")

    manager.void_order.assert_not_awaited()
    await client.aclose()
