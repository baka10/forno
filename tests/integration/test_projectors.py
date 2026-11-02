"""Testcontainers: Postgres. `KitchenProjector`/`StatsProjector.apply_event`
take a plain `AsyncSession` + payload dict, decoupled from Kafka consumption
— exercised directly here rather than through a real broker. See
CONTRIBUTING.md "Adding a metric": these two metrics answer "is the kitchen
falling behind on this stage?" and "are orders piling up faster than they
close?" — both silent before this test existed (`forno_item_stage_seconds`
was declared but never observed anywhere).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select

from forno.adapters.db.models import MenuItemModel, TicketLineModel
from forno.adapters.db.repositories import SqlAlchemyOrderRepository
from forno.adapters.messaging.consumers import KitchenProjector, StatsProjector
from forno.domain.ids import MenuItemId, OrderId, OrderItemId
from forno.domain.item import ItemStatus, OrderItem
from forno.domain.money import Currency, Money, VatRate
from forno.domain.order import Channel, Order, OrderStatus
from forno.observability import metrics

pytestmark = pytest.mark.integration

_T0 = datetime(2026, 7, 26, 12, 0, tzinfo=UTC)


async def _seed_order(session_factory) -> tuple[str, str]:
    """A real `orders`/`order_items` row pair — `ticket_lines.order_item_id`
    is a foreign key, so the projector tests need a real item to point at,
    not a bare UUID."""
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
        await session.commit()
        menu_item_id = MenuItemId(menu_item.id)

    order_id = OrderId(uuid4())
    item = OrderItem(
        id=OrderItemId(uuid4()),
        order_id=order_id,
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
        id=order_id,
        channel=Channel.TAKEAWAY,
        table_id=None,
        status=OrderStatus.ACCEPTED,
        items=[item],
        placed_at=_T0,
    )
    async with session_factory() as session:
        await SqlAlchemyOrderRepository(session).add(order)
        await session.commit()

    return str(order.id), str(item.id)


async def test_item_stage_seconds_observes_queue_to_prep_duration(session_factory) -> None:
    order_id, item_id = await _seed_order(session_factory)
    projector = KitchenProjector(session_factory, consumer=None, dlq_producer=None, max_retries=1)

    before_sum = metrics.item_stage_seconds.labels(stage="queue")._sum.get()

    async with session_factory() as session:
        await projector.apply_event(
            session,
            "OrderPlaced",
            {
                "order_id": order_id,
                "occurred_at": _T0.isoformat(),
                "items": [{"order_item_id": item_id}],
            },
        )
        await session.commit()

    async with session_factory() as session:
        await projector.apply_event(
            session,
            "ItemStateChanged",
            {
                "item_id": item_id,
                "status": "PREPPING",
                "occurred_at": (_T0 + timedelta(seconds=45)).isoformat(),
            },
        )
        await session.commit()

    after_sum = metrics.item_stage_seconds.labels(stage="queue")._sum.get()
    assert after_sum == pytest.approx(before_sum + 45.0)

    async with session_factory() as session:
        line = (
            await session.execute(
                select(TicketLineModel).where(TicketLineModel.order_item_id == item_id)
            )
        ).scalar_one()
    assert line.status == "PREPPING"
    assert line.status_changed_at == _T0 + timedelta(seconds=45)


async def test_orders_open_gauge_tracks_wip(session_factory) -> None:
    order_id, _item_id = await _seed_order(session_factory)
    projector = StatsProjector(session_factory, consumer=None, dlq_producer=None, max_retries=1)

    async with session_factory() as session:
        await projector.apply_event(
            session,
            "OrderPlaced",
            {
                "order_id": order_id,
                "channel": "takeaway",
                "occurred_at": _T0.isoformat(),
                "items": [{"unit_price_snapshot": "32.00"}],
            },
        )
        await session.commit()

    open_after_place = metrics.orders_open.labels(channel="takeaway")._value.get()
    assert open_after_place >= 1

    async with session_factory() as session:
        await projector.apply_event(
            session,
            "OrderClosed",
            {"order_id": order_id, "occurred_at": (_T0 + timedelta(minutes=20)).isoformat()},
        )
        await session.commit()

    open_after_close = metrics.orders_open.labels(channel="takeaway")._value.get()
    assert open_after_close == open_after_place - 1
