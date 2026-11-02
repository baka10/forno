from __future__ import annotations

from random import Random
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from simulator.actors.customer import CustomerActor
from simulator.catalog import MenuCatalog
from simulator.clock import ScaledClock
from simulator.http_client import FornoClient
from simulator.models import Channel
from simulator.scenario import BehaviourConfig, DeliberationConfig, Scenario
from simulator.shadow_ledger import ShadowLedger


def _behaviour(**overrides: Any) -> BehaviourConfig:  # noqa: ANN401 -- test helper, freeform field overrides
    defaults = {
        "deliberation_seconds": DeliberationConfig(min=1, max=2),
        "remake_rate": 0.0,
        "walkout_after_seconds": 60,
        "double_submit_rate": 0.0,
    }
    defaults.update(overrides)
    return BehaviourConfig(**defaults)


def _customer(  # noqa: PLR0913 -- test helper: one optional collaborator per parameter
    tiny_scenario_path: str,
    cashier: Any = None,  # noqa: ANN401 -- test helper, accepts either a real collaborator or a mock
    waiter_client: Any = None,  # noqa: ANN401 -- same
    order_status_client: Any = None,  # noqa: ANN401 -- same
    behaviour: BehaviourConfig | None = None,
    ledger: ShadowLedger | None = None,
) -> tuple[CustomerActor, MenuCatalog]:
    scenario = Scenario.from_yaml(tiny_scenario_path)
    catalog = MenuCatalog()
    for i, item in enumerate(scenario.pizzeria.menu):
        catalog.register(f"menu-{i}", item)

    customer = CustomerActor(
        cashier=cashier or AsyncMock(),
        waiter_client=waiter_client or AsyncMock(),
        order_status_client=order_status_client or AsyncMock(),
        clock=ScaledClock(time_scale=1000),
        catalog=catalog,
        ledger=ledger or ShadowLedger(),
        behaviour=behaviour or _behaviour(),
        rng=Random(1),
    )
    return customer, catalog


def test_decide_order_returns_one_line_per_distinct_item_ordered(tiny_scenario_path: str) -> None:
    customer, catalog = _customer(tiny_scenario_path)

    lines = customer.decide_order(party_size=4)

    assert sum(line["quantity"] for line in lines) == 4
    assert all(line["fire_group"] == 1 for line in lines)
    assert all(line["menu_item_id"] in catalog.by_id for line in lines)


def test_decide_order_is_deterministic_for_a_given_seed(tiny_scenario_path: str) -> None:
    customer_a, _ = _customer(tiny_scenario_path)
    customer_b, _ = _customer(tiny_scenario_path)

    assert customer_a.decide_order(3) == customer_b.decide_order(3)


def test_should_walk_out_respects_the_configured_threshold(tiny_scenario_path: str) -> None:
    customer, _ = _customer(tiny_scenario_path, behaviour=_behaviour(walkout_after_seconds=100))

    assert not customer.should_walk_out(elapsed_seconds=99)
    assert customer.should_walk_out(elapsed_seconds=100)


@pytest.mark.asyncio
async def test_visit_seats_and_clears_a_dine_in_table(tiny_scenario_path: str) -> None:
    cashier = AsyncMock()
    cashier.place_order.return_value = None  # order rejected — shortest path through visit()
    waiter_client = AsyncMock()
    customer, _ = _customer(tiny_scenario_path, cashier=cashier, waiter_client=waiter_client)

    await customer.visit(party_size=2, channel=Channel.DINE_IN, table_number=3)

    waiter_client.post.assert_any_call("/tables/3/seat", {"party_size": 2})
    waiter_client.post.assert_any_call("/tables/3/clear", {})


@pytest.mark.asyncio
async def test_visit_takeaway_never_touches_tables(tiny_scenario_path: str) -> None:
    cashier = AsyncMock()
    cashier.place_order.return_value = None
    waiter_client = AsyncMock()
    customer, _ = _customer(tiny_scenario_path, cashier=cashier, waiter_client=waiter_client)

    await customer.visit(party_size=1, channel=Channel.TAKEAWAY, table_number=None)

    waiter_client.post.assert_not_awaited()


@pytest.mark.asyncio
async def test_visit_closes_order_once_served(tiny_scenario_path: str) -> None:
    cashier = AsyncMock()
    cashier.place_order.return_value = {
        "id": "order-1",
        "placed_at": "2026-01-01T18:20:00+00:00",
        "promised_at": "2026-01-01T18:30:00+00:00",
        "items": [{"id": "item-1", "menu_item_id": "menu-0"}],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"status": "SERVED", "ready_at": "2026-01-01T18:31:00+00:00"}
        )

    order_status_client = FornoClient(
        "http://testserver", "dev", "cashier", transport=httpx.MockTransport(handler)
    )
    waiter_client = AsyncMock()
    ledger = ShadowLedger()
    customer, _ = _customer(
        tiny_scenario_path,
        cashier=cashier,
        waiter_client=waiter_client,
        order_status_client=order_status_client,
        ledger=ledger,
    )

    await customer.visit(party_size=1, channel=Channel.TAKEAWAY, table_number=None)

    cashier.close_order.assert_awaited_once_with("order-1")
    # Ready 60 wall seconds after the promise; at the test clock's 1000x that
    # is 60000 domain seconds late, scored exactly once.
    assert ledger.orders_served == 1
    assert ledger.promise_deltas_seconds == [60000.0]


@pytest.mark.asyncio
async def test_walkout_scores_the_promise_as_late_by_at_least_the_wait(
    tiny_scenario_path: str,
) -> None:
    """A customer who gives up before the food is ready is a breach the
    kitchen never got to record — the ledger counts it as late by the wait
    they walked out on, a lower bound on the real lateness."""
    cashier = AsyncMock()
    cashier.place_order.return_value = {
        "id": "order-1",
        "placed_at": "2026-01-01T18:20:00+00:00",
        # 0.1 wall s after placing = 100 domain s at the test clock's 1000x.
        "promised_at": "2026-01-01T18:20:00.100000+00:00",
        "items": [{"id": "item-1", "menu_item_id": "menu-0"}],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "IN_KITCHEN", "ready_at": None})

    order_status_client = FornoClient(
        "http://testserver", "dev", "cashier", transport=httpx.MockTransport(handler)
    )
    ledger = ShadowLedger()
    customer, _ = _customer(
        tiny_scenario_path,
        cashier=cashier,
        waiter_client=AsyncMock(),
        order_status_client=order_status_client,
        behaviour=_behaviour(walkout_after_seconds=240),
        ledger=ledger,
    )

    await customer.visit(party_size=1, channel=Channel.TAKEAWAY, table_number=None)

    assert ledger.walkouts == 1
    assert ledger.orders_served == 0
    assert len(ledger.promise_deltas_seconds) == 1
    assert ledger.promise_deltas_seconds[0] >= 240 - 100
