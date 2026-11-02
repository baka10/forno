"""`OrderRepository` implementation. See docs/adr/0010-abstraction-budget.md —
justified as the single guarded write path, not as database swappability.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import CursorResult, select, update
from sqlalchemy.orm import selectinload

from forno.adapters.db.models import OrderItemModel, OrderModel
from forno.domain.exceptions import ConcurrentModificationError, UnknownEntityError
from forno.domain.ids import MenuItemId, OrderId, OrderItemId, TableId
from forno.domain.item import ItemStatus, OrderItem, Priority
from forno.domain.money import Currency, Money, VatRate
from forno.domain.order import Channel, Order, OrderStatus
from forno.domain.ports import OrderRepository

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# The schema carries no currency column: a single Forno deployment prices one
# pizzeria in one currency. See docs/domain-model.md "Money".
_CURRENCY = Currency.PLN


def _item_to_domain(model: OrderItemModel) -> OrderItem:
    return OrderItem(
        id=OrderItemId(model.id),
        order_id=OrderId(model.order_id),
        menu_item_id=MenuItemId(model.menu_item_id),
        name_snapshot=model.name_snapshot,
        unit_price_snapshot=Money.from_decimal(model.unit_price_snapshot, _CURRENCY),
        vat_rate_snapshot=VatRate(model.vat_rate_snapshot),
        prep_seconds=model.prep_seconds,
        bake_seconds=model.bake_seconds,
        fire_group=model.fire_group,
        status=ItemStatus(model.status),
        priority=Priority(model.priority),
        remake_of=OrderItemId(model.remake_of) if model.remake_of is not None else None,
        oven_id=model.oven_id,
        oven_slot=model.oven_slot,
    )


def _item_to_model(item: OrderItem) -> OrderItemModel:
    return OrderItemModel(
        id=item.id,
        order_id=item.order_id,
        menu_item_id=item.menu_item_id,
        name_snapshot=item.name_snapshot,
        unit_price_snapshot=item.unit_price_snapshot.to_decimal(),
        vat_rate_snapshot=item.vat_rate_snapshot.rate,
        prep_seconds=item.prep_seconds,
        bake_seconds=item.bake_seconds,
        fire_group=item.fire_group,
        status=item.status.value,
        priority=item.priority.value,
        remake_of=item.remake_of,
        oven_id=item.oven_id,
        oven_slot=item.oven_slot,
    )


def order_model_to_domain(model: OrderModel) -> Order:
    return Order(
        id=OrderId(model.id),
        channel=Channel(model.channel),
        table_id=TableId(model.table_number) if model.table_number is not None else None,
        status=OrderStatus(model.status),
        items=[_item_to_domain(item) for item in model.items],
        placed_at=model.placed_at,
        promised_at=model.promised_at,
        ready_at=model.ready_at,
        version=model.version,
    )


class SqlAlchemyOrderRepository(OrderRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, order_id: OrderId) -> Order:
        stmt = (
            select(OrderModel)
            .where(OrderModel.id == order_id)
            .options(selectinload(OrderModel.items))
        )
        model = (await self._session.execute(stmt)).scalar_one_or_none()
        if model is None:
            msg = f"Order {order_id} not found"
            raise UnknownEntityError(msg)
        return order_model_to_domain(model)

    async def add(self, order: Order) -> None:
        model = OrderModel(
            id=order.id,
            status=order.status.value,
            channel=order.channel.value,
            table_number=order.table_id,
            placed_at=order.placed_at,
            promised_at=order.promised_at,
            ready_at=order.ready_at,
            total_gross=order.total_gross.to_decimal(),
            version=order.version,
        )
        self._session.add(model)
        for item in order.items:
            self._session.add(_item_to_model(item))
        await self._session.flush()

    async def save(self, order: Order) -> None:
        """Optimistic-locked update keyed on `Order.version` — raises on
        concurrent modification rather than silently overwriting."""
        stmt = (
            update(OrderModel)
            .where(OrderModel.id == order.id, OrderModel.version == order.version)
            .values(
                status=order.status.value,
                promised_at=order.promised_at,
                ready_at=order.ready_at,
                total_gross=order.total_gross.to_decimal(),
                version=order.version + 1,
            )
        )
        result = cast("CursorResult[Any]", await self._session.execute(stmt))
        if result.rowcount == 0:
            msg = f"Order {order.id} was modified concurrently (expected version {order.version})"
            raise ConcurrentModificationError(msg)
        order.version += 1

        for item in order.items:
            await self._session.merge(_item_to_model(item))
        await self._session.flush()
