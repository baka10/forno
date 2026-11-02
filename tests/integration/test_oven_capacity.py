"""The oven-slot boundary is the one capacity constraint actually enforced
today (see the "where's the capacity configured" discussion) — `FOR UPDATE
SKIP LOCKED` against real `oven_slots` rows, not an in-memory counter. This
is the "crucial border" integration test for it: configure N slots, allocate
N items, confirm the (N+1)th is rejected with `CapacityExceededError`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from forno.adapters.db.models import KitchenTicketModel, MenuItemModel, OvenModel, OvenSlotModel
from forno.adapters.db.unit_of_work import SqlAlchemyUnitOfWork
from forno.application.use_cases.kitchen import OvenInItem
from forno.domain.clock import FrozenClock
from forno.domain.exceptions import CapacityExceededError
from forno.domain.ids import KitchenTicketId, MenuItemId, OrderId, OrderItemId
from forno.domain.item import ItemStatus, OrderItem
from forno.domain.money import Currency, Money, VatRate
from forno.domain.order import Channel, Order, OrderStatus

pytestmark = pytest.mark.integration

_NOW = datetime(2026, 7, 22, 18, 0, tzinfo=UTC)
_OVEN_SLOTS = 2


@pytest.fixture
def clock() -> FrozenClock:
    return FrozenClock(at=_NOW)


async def _seed_order_in_kitchen(
    session_factory, item_count: int
) -> tuple[OrderId, KitchenTicketId, list[OrderItemId]]:
    from forno.adapters.db.repositories import SqlAlchemyOrderRepository

    oven_id = f"deck-{uuid4().hex[:6]}"
    async with session_factory() as session:
        menu_item = MenuItemModel(
            sku=f"TEST-{uuid4().hex[:8]}",
            name="Margherita",
            price=Decimal("32.00"),
            vat_rate=Decimal("0.08"),
            prep_seconds=60,
            bake_seconds=300,
            available=True,
        )
        session.add(menu_item)
        session.add(OvenModel(id=oven_id, slots=_OVEN_SLOTS, bake_seconds=300, active=True))
        await session.flush()
        for slot in range(1, _OVEN_SLOTS + 1):
            session.add(
                OvenSlotModel(oven_id=oven_id, slot=slot, order_item_id=None, release_time=None)
            )
        await session.commit()
        menu_item_id = MenuItemId(menu_item.id)

    items = [
        OrderItem(
            id=OrderItemId(uuid4()),
            order_id=OrderId(uuid4()),
            menu_item_id=menu_item_id,
            name_snapshot="Margherita",
            unit_price_snapshot=Money(3200, Currency.PLN),
            vat_rate_snapshot=VatRate(Decimal("0.08")),
            prep_seconds=60,
            bake_seconds=300,
            fire_group=1,
            status=ItemStatus.PREPPING,
        )
        for _ in range(item_count)
    ]
    order = Order(
        id=OrderId(uuid4()),
        channel=Channel.DINE_IN,
        table_id=None,
        status=OrderStatus.IN_KITCHEN,
        items=items,
        placed_at=_NOW,
    )
    for item in items:
        item.order_id = order.id

    async with session_factory() as session:
        repo = SqlAlchemyOrderRepository(session)
        await repo.add(order)
        ticket = KitchenTicketModel(order_id=order.id, status="OPEN", created_at=_NOW)
        session.add(ticket)
        await session.commit()
        ticket_id = KitchenTicketId(ticket.id)

    return order.id, ticket_id, [item.id for item in items]


async def test_oven_in_succeeds_up_to_configured_slot_count_then_422s(session_factory, clock):
    _order_id, ticket_id, item_ids = await _seed_order_in_kitchen(
        session_factory, item_count=_OVEN_SLOTS + 1
    )

    # Fill every configured slot — each must succeed.
    for item_id in item_ids[:_OVEN_SLOTS]:
        uow = SqlAlchemyUnitOfWork(session_factory)
        await OvenInItem(uow, clock).execute(ticket_id, item_id, "c-1")

    # The next item finds no free slot.
    uow = SqlAlchemyUnitOfWork(session_factory)
    with pytest.raises(CapacityExceededError):
        await OvenInItem(uow, clock).execute(ticket_id, item_ids[_OVEN_SLOTS], "c-1")


async def test_oven_slot_freed_by_ready_becomes_available_again(session_factory, clock):
    from forno.application.use_cases.kitchen import MarkItemReady

    _order_id, ticket_id, item_ids = await _seed_order_in_kitchen(
        session_factory, item_count=_OVEN_SLOTS + 1
    )

    for item_id in item_ids[:_OVEN_SLOTS]:
        uow = SqlAlchemyUnitOfWork(session_factory)
        await OvenInItem(uow, clock).execute(ticket_id, item_id, "c-1")

    # Pull the first item out of the oven — its slot should free up.
    uow = SqlAlchemyUnitOfWork(session_factory)
    await MarkItemReady(uow, clock).execute(ticket_id, item_ids[0], "c-1")

    # Now the previously-rejected third item fits.
    uow = SqlAlchemyUnitOfWork(session_factory)
    order = await OvenInItem(uow, clock).execute(ticket_id, item_ids[_OVEN_SLOTS], "c-1")
    assert order.get_item(item_ids[_OVEN_SLOTS]).status == ItemStatus.IN_OVEN
