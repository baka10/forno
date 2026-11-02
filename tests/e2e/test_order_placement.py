"""The one scenario the whole stack has to get right: place an order over
real HTTP, against a real Postgres, through the real FastAPI app — and see
it again. Kept deliberately narrow; deeper scenarios belong in the
integration suite instead.
"""

from __future__ import annotations

from uuid import uuid4

import httpx
import pytest

pytestmark = pytest.mark.e2e

_MANAGER = {"X-API-Key": "dev", "X-Role": "manager"}
_CASHIER = {"X-API-Key": "dev", "X-Role": "cashier"}


async def _create_menu_item(client: httpx.AsyncClient) -> str:
    # sku is unique in the schema; the Postgres container is shared across
    # every test in this session, so each call needs its own.
    response = await client.post(
        "/menu/items",
        headers=_MANAGER,
        json={
            "sku": f"MARG-{uuid4().hex[:8]}",
            "name": "Margherita",
            "price": "32.00",
            "vat": "0.08",
            "prep_seconds": 60,
            "bake_seconds": 300,
        },
    )
    assert response.status_code == httpx.codes.CREATED, response.text
    return response.json()["id"]


async def test_placed_order_is_visible_by_id(client: httpx.AsyncClient):
    menu_item_id = await _create_menu_item(client)

    place = await client.post(
        "/orders",
        headers=_CASHIER,
        json={
            "channel": "dine_in",
            "lines": [{"menu_item_id": menu_item_id, "quantity": 2, "fire_group": 1}],
        },
    )
    assert place.status_code == httpx.codes.CREATED, place.text
    placed = place.json()
    assert placed["status"] == "ACCEPTED"
    assert placed["promised_at"] is not None
    assert placed["total_gross"] == "69.12"  # 2 * 32.00 * 1.08

    fetched = await client.get(f"/orders/{placed['id']}")
    assert fetched.status_code == httpx.codes.OK
    assert fetched.json() == placed


async def test_placed_order_appears_in_list_orders(client: httpx.AsyncClient):
    menu_item_id = await _create_menu_item(client)
    place = await client.post(
        "/orders",
        headers=_CASHIER,
        json={
            "channel": "takeaway",
            "lines": [{"menu_item_id": menu_item_id, "quantity": 1, "fire_group": 1}],
        },
    )
    assert place.status_code == httpx.codes.CREATED
    order_id = place.json()["id"]

    listed = await client.get("/orders", params={"limit": 50})
    assert listed.status_code == httpx.codes.OK
    assert any(order["id"] == order_id for order in listed.json())


async def test_unknown_order_id_returns_404(client: httpx.AsyncClient):
    response = await client.get("/orders/00000000-0000-0000-0000-000000000000")
    assert response.status_code == httpx.codes.NOT_FOUND
    problem = response.json()
    assert problem["status"] == httpx.codes.NOT_FOUND
    assert "request_id" in problem  # RFC 9457 shape, not a generic FastAPI 404
