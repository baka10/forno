"""`SqlAlchemyOrderRepository` against a real Postgres — the one write path
every order mutation goes through. See docs/adr/0010-abstraction-budget.md.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
import pytest_asyncio

from forno.adapters.db.models import MenuItemModel
from forno.adapters.db.repositories import SqlAlchemyOrderRepository
from forno.domain.exceptions import ConcurrentModificationError, UnknownEntityError
from forno.domain.ids import MenuItemId, OrderId, OrderItemId
from forno.domain.item import ItemStatus, OrderItem
from forno.domain.money import Currency, Money, VatRate
from forno.domain.order import Channel, Order, OrderStatus

pytestmark = pytest.mark.integration

_NOW = datetime(2026, 7, 22, 18, 0, tzinfo=UTC)


@pytest_asyncio.fixture
async def menu_item_id(session_factory) -> MenuItemId:
    """`order_items.menu_item_id` is a real foreign key — seed a row it can
    reference, the way `PlaceOrder` would have via `POST /menu/items`."""
    menu_item = MenuItemModel(
        sku=f"TEST-{uuid4().hex[:8]}",
        name="Margherita",
        price=Decimal("32.00"),
        vat_rate=Decimal("0.08"),
        prep_seconds=60,
        bake_seconds=300,
        available=True,
    )
    async with session_factory() as session:
        session.add(menu_item)
        await session.commit()
        return MenuItemId(menu_item.id)


def _order_with_item(menu_item_id: MenuItemId) -> Order:
    item = OrderItem(
        id=OrderItemId(uuid4()),
        order_id=OrderId(uuid4()),
        menu_item_id=menu_item_id,
        name_snapshot="Margherita",
        unit_price_snapshot=Money(3200, Currency.PLN),
        vat_rate_snapshot=VatRate(Decimal("0.08")),
        prep_seconds=60,
        bake_seconds=300,
        fire_group=1,
        status=ItemStatus.QUEUED,
    )
    order = Order(
        id=OrderId(uuid4()),
        channel=Channel.DINE_IN,
        table_id=None,
        status=OrderStatus.PLACED,
        items=[item],
        placed_at=_NOW,
    )
    item.order_id = order.id
    return order


async def test_add_then_get_round_trips_order_and_items(session_factory, menu_item_id):
    order = _order_with_item(menu_item_id)
    async with session_factory() as session:
        repo = SqlAlchemyOrderRepository(session)
        await repo.add(order)
        await session.commit()

    async with session_factory() as session:
        repo = SqlAlchemyOrderRepository(session)
        fetched = await repo.get(order.id)

    assert fetched.id == order.id
    assert fetched.status == OrderStatus.PLACED
    assert fetched.channel == Channel.DINE_IN
    assert len(fetched.items) == 1
    assert fetched.items[0].name_snapshot == "Margherita"
    assert fetched.items[0].unit_price_snapshot == Money(3200, Currency.PLN)
    assert fetched.version == 0


async def test_get_unknown_order_raises_unknown_entity(session_factory):
    async with session_factory() as session:
        repo = SqlAlchemyOrderRepository(session)
        with pytest.raises(UnknownEntityError):
            await repo.get(OrderId(uuid4()))


async def test_save_persists_status_and_bumps_version(session_factory, menu_item_id):
    order = _order_with_item(menu_item_id)
    async with session_factory() as session:
        repo = SqlAlchemyOrderRepository(session)
        await repo.add(order)
        await session.commit()

    async with session_factory() as session:
        repo = SqlAlchemyOrderRepository(session)
        fetched = await repo.get(order.id)
        fetched.accept(promised_at=_NOW)
        await repo.save(fetched)
        await session.commit()
        assert fetched.version == 1

    async with session_factory() as session:
        repo = SqlAlchemyOrderRepository(session)
        refetched = await repo.get(order.id)
    assert refetched.status == OrderStatus.ACCEPTED
    assert refetched.version == 1


async def test_concurrent_save_raises_concurrent_modification(session_factory, menu_item_id):
    """Two sessions load the same order; the first to commit wins and the
    second's stale `version` must be rejected rather than silently
    overwriting the first update."""
    order = _order_with_item(menu_item_id)
    async with session_factory() as session:
        repo = SqlAlchemyOrderRepository(session)
        await repo.add(order)
        await session.commit()

    async with session_factory() as session_a, session_factory() as session_b:
        repo_a = SqlAlchemyOrderRepository(session_a)
        repo_b = SqlAlchemyOrderRepository(session_b)

        order_a = await repo_a.get(order.id)
        order_b = await repo_b.get(order.id)

        order_a.accept(promised_at=_NOW)
        await repo_a.save(order_a)
        await session_a.commit()

        order_b.accept(promised_at=_NOW)
        with pytest.raises(ConcurrentModificationError):
            await repo_b.save(order_b)
