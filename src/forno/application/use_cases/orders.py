"""Ordering use cases. See docs/api.md "Ordering — cashier" and
docs/architecture.md "Placing an order: the critical path".

Idempotency-Key handling (lookup, replay, conflict-on-reuse) is cross-cutting
and lives at the HTTP edge (adapters/http), not duplicated in each use case —
see docs/api.md "Idempotency".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from forno.adapters.db.models import (
    KitchenTicketModel,
    MenuItemModel,
    OrderItemModel,
    OrderModel,
    OvenModel,
    OvenSlotModel,
    ShiftModel,
    StationModel,
    TableModel,
    TicketLineModel,
)
from forno.adapters.db.repositories import order_model_to_domain
from forno.application.parsing import parse_timestamp, parse_uuid
from forno.domain.events import (
    ItemStateChanged,
    OrderAccepted,
    OrderCancelled,
    OrderClosed,
    OrderPlaced,
    OrderPlacedLine,
    OrderServed,
    OrderVoided,
)
from forno.domain.exceptions import (
    InvalidArgumentError,
    MenuItemUnavailableError,
    UnknownEntityError,
)
from forno.domain.ids import MenuItemId, OrderId, OrderItemId, OvenId, StationId, TableId
from forno.domain.item import ItemStatus, OrderItem, Priority
from forno.domain.kitchen_state import (
    KitchenState,
    OvenSlotState,
    OvenState,
    QueuedItem,
    StationState,
)
from forno.domain.money import Currency, Money, VatRate
from forno.domain.order import Channel, Order, OrderStatus
from forno.observability import metrics

if TYPE_CHECKING:
    from datetime import datetime

    from forno.application.unit_of_work import UnitOfWork
    from forno.domain.clock import Clock
    from forno.domain.ports import PromiseEstimator

# The schema carries no currency column — see adapters/db/repositories.py.
_CURRENCY = Currency.PLN


@dataclass(slots=True)
class PlaceOrderLine:
    menu_item_id: str
    quantity: int
    fire_group: int


async def _kitchen_state(uow: UnitOfWork, now: datetime, time_scale: int) -> KitchenState:
    ovens = (
        (await uow.session.execute(select(OvenModel).where(OvenModel.active.is_(True))))
        .scalars()
        .all()
    )
    oven_states = []
    for oven in ovens:
        slots = (
            (
                await uow.session.execute(
                    select(OvenSlotModel).where(OvenSlotModel.oven_id == oven.id)
                )
            )
            .scalars()
            .all()
        )
        oven_states.append(
            OvenState(
                oven_id=OvenId(oven.id),
                bake_seconds=oven.bake_seconds,
                slots=tuple(OvenSlotState(slot=s.slot, release_time=s.release_time) for s in slots),
            )
        )
    # The cross-order backlog: every item on an open ticket that still needs
    # a chef (QUEUED) or is in a chef's hands (PREPPING). Items already in the
    # oven are accounted for by the slot release times above.
    backlog_rows = (
        await uow.session.execute(
            select(OrderItemModel, TicketLineModel.status)
            .join(TicketLineModel, TicketLineModel.order_item_id == OrderItemModel.id)
            .join(KitchenTicketModel, KitchenTicketModel.id == TicketLineModel.ticket_id)
            .where(
                KitchenTicketModel.status == "OPEN",
                TicketLineModel.status.in_(("QUEUED", "PREPPING")),
            )
        )
    ).all()
    queued = tuple(
        QueuedItem(
            item_id=OrderItemId(item.id),
            prep_seconds=item.prep_seconds,
            bake_seconds=item.bake_seconds,
            fire_group=item.fire_group,
            priority=Priority(item.priority),
        )
        for item, _status in backlog_rows
    )
    prepping = sum(1 for _item, status in backlog_rows if status == "PREPPING")

    # A chef is the unit of prep capacity: the latest shift's head count wins
    # over the prep station's counter space when both are known.
    latest_shift = (
        await uow.session.execute(
            select(ShiftModel).order_by(ShiftModel.started_at.desc()).limit(1)
        )
    ).scalar_one_or_none()
    stations = (await uow.session.execute(select(StationModel))).scalars().all()
    station_states = []
    for station in stations:
        capacity = station.capacity
        claims = 0
        if station.id == "prep":
            claims = prepping
            if latest_shift is not None and latest_shift.chefs > 0:
                capacity = latest_shift.chefs
        station_states.append(
            StationState(station_id=StationId(station.id), capacity=capacity, current_claims=claims)
        )
    return KitchenState(
        now=now,
        ovens=tuple(oven_states),
        stations=tuple(station_states),
        queued=queued,
        time_scale=time_scale,
    )


class PlaceOrder:
    """Builds the aggregate, snapshots prices, computes the promise, and
    persists aggregate + outbox row in one transaction. See
    docs/architecture.md sequence diagram, steps 1-9."""

    def __init__(self, uow: UnitOfWork, estimator: PromiseEstimator, clock: Clock) -> None:
        self._uow = uow
        self._estimator = estimator
        self._clock = clock

    async def execute(
        self,
        channel: Channel,
        table_number: int | None,
        lines: list[PlaceOrderLine],
        correlation_id: str,
    ) -> Order:
        if not lines:
            msg = "An order needs at least one line"
            raise InvalidArgumentError(msg)

        async with self._uow:
            order_id = OrderId(uuid4())
            now = self._clock.now()

            table: TableModel | None = None
            if table_number is not None:
                table = await self._uow.session.get(TableModel, table_number)
                if table is None:
                    msg = f"Table {table_number} not found"
                    raise UnknownEntityError(msg)

            items: list[OrderItem] = []
            sold: list[tuple[str, int]] = []
            for line in lines:
                menu_item = await self._uow.session.get(
                    MenuItemModel, parse_uuid(line.menu_item_id, "menu_item_id")
                )
                if menu_item is None or not menu_item.available:
                    msg = f"Menu item {line.menu_item_id} is not available"
                    raise MenuItemUnavailableError(msg)
                items.extend(
                    OrderItem(
                        id=OrderItemId(uuid4()),
                        order_id=order_id,
                        menu_item_id=MenuItemId(menu_item.id),
                        name_snapshot=menu_item.name,
                        unit_price_snapshot=Money.from_decimal(menu_item.price, _CURRENCY),
                        vat_rate_snapshot=VatRate(menu_item.vat_rate),
                        prep_seconds=menu_item.prep_seconds,
                        bake_seconds=menu_item.bake_seconds,
                        fire_group=line.fire_group,
                    )
                    for _ in range(line.quantity)
                )
                sold.append((menu_item.sku, line.quantity))

            order = Order(
                id=order_id,
                channel=channel,
                table_id=TableId(table_number) if table_number is not None else None,
                status=OrderStatus.PLACED,
                items=items,
                placed_at=now,
            )

            state = await _kitchen_state(self._uow, now, self._clock.time_scale)
            promise = self._estimator.promise(order, state)
            order.accept(promised_at=promise.at)

            await self._uow.orders.add(order)
            if table is not None:
                table.current_order_id = order.id

            await self._uow.events.publish(
                order.id,
                OrderPlaced(
                    order_id=order.id,
                    channel=order.channel.value,
                    items=tuple(
                        OrderPlacedLine(
                            item_id=item.id,
                            menu_item_id=item.menu_item_id,
                            name_snapshot=item.name_snapshot,
                            unit_price_snapshot=item.unit_price_snapshot,
                            fire_group=item.fire_group,
                        )
                        for item in order.items
                    ),
                    occurred_at=now,
                ),
                correlation_id,
            )
            await self._uow.events.publish(
                order.id,
                OrderAccepted(order_id=order.id, promised_at=promise.at, occurred_at=now),
                correlation_id,
            )

            await self._uow.commit()

        # Only after the commit: a placement that failed on a later line (or
        # on the write itself) must not count as a sale.
        for sku, quantity in sold:
            metrics.items_sold_total.labels(menu_item=sku, size="standard").inc(quantity)
        return order


class CancelOrder:
    """Raises IllegalTransitionError (-> 409) once any item is IN_OVEN. See
    docs/domain-model.md invariant #8."""

    def __init__(self, uow: UnitOfWork, clock: Clock) -> None:
        self._uow = uow
        self._clock = clock

    async def execute(self, order_id: OrderId, correlation_id: str) -> Order:
        async with self._uow:
            order = await self._uow.orders.get(order_id)
            order.cancel()
            await self._uow.orders.save(order)
            await self._uow.events.publish(
                order.id,
                OrderCancelled(order_id=order.id, occurred_at=self._clock.now()),
                correlation_id,
            )
            await self._uow.commit()
            return order


class VoidOrder:
    """Manager only. The write-off for an order that was made but will not be
    paid for (walkout, complaint) — distinct from cancel. Pizzas still in the
    oven come out: their slots are released, since no chef will ever mark
    them ready. See docs/domain-model.md "Order lifecycle"."""

    def __init__(self, uow: UnitOfWork, clock: Clock) -> None:
        self._uow = uow
        self._clock = clock

    async def execute(self, order_id: OrderId, reason: str, correlation_id: str) -> Order:
        async with self._uow:
            order = await self._uow.orders.get(order_id)
            order.void(reason)
            await self._release_oven_slots(order)
            await self._uow.orders.save(order)
            await self._uow.events.publish(
                order.id,
                OrderVoided(order_id=order.id, reason=reason, occurred_at=self._clock.now()),
                correlation_id,
            )
            await self._uow.commit()
            return order

    async def _release_oven_slots(self, order: Order) -> None:
        baking = [item.id for item in order.items if item.status == ItemStatus.IN_OVEN]
        if not baking:
            return
        slots = (
            await self._uow.session.execute(
                select(OvenSlotModel).where(OvenSlotModel.order_item_id.in_(baking))
            )
        ).scalars()
        for slot in slots:
            slot.order_item_id = None
            slot.release_time = None
            metrics.oven_slots_occupied.labels(oven=slot.oven_id).dec()


class CloseOrder:
    def __init__(self, uow: UnitOfWork, clock: Clock) -> None:
        self._uow = uow
        self._clock = clock

    async def execute(self, order_id: OrderId, correlation_id: str) -> Order:
        async with self._uow:
            order = await self._uow.orders.get(order_id)
            order.close()
            await self._uow.orders.save(order)
            await self._uow.events.publish(
                order.id,
                OrderClosed(order_id=order.id, occurred_at=self._clock.now()),
                correlation_id,
            )
            await self._uow.commit()
            return order


class DeliverItem:
    """Idempotent — double-delivery is invariant #1 in the simulator's shadow
    ledger. See docs/api.md "Service — waiter"."""

    def __init__(self, uow: UnitOfWork, clock: Clock) -> None:
        self._uow = uow
        self._clock = clock

    async def execute(self, order_id: OrderId, item_id: str, correlation_id: str) -> Order:
        async with self._uow:
            order = await self._uow.orders.get(order_id)
            now = self._clock.now()
            was_served = order.status == OrderStatus.SERVED

            item_ref = OrderItemId(parse_uuid(item_id, "item_id"))
            if order.deliver_item(item_ref):
                item = order.get_item(item_ref)
                await self._uow.events.publish(
                    order.id,
                    ItemStateChanged(
                        order_id=order.id,
                        item_id=item.id,
                        ticket_id=None,
                        status=item.status.value,
                        occurred_at=now,
                    ),
                    correlation_id,
                )

            if order.status == OrderStatus.SERVED and not was_served:
                await self._uow.events.publish(
                    order.id, OrderServed(order_id=order.id, occurred_at=now), correlation_id
                )

            await self._uow.orders.save(order)
            await self._uow.commit()
            return order


class GetOrder:
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    async def execute(self, order_id: OrderId) -> Order:
        async with self._uow:
            return await self._uow.orders.get(order_id)


class ListOrders:
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    async def execute(
        self,
        status: str | None = None,
        table: int | None = None,
        since: str | None = None,
        limit: int = 50,
    ) -> list[Order]:
        async with self._uow:
            stmt = (
                select(OrderModel)
                .options(selectinload(OrderModel.items))
                .order_by(OrderModel.placed_at.desc())
                .limit(limit)
            )
            if status is not None:
                stmt = stmt.where(OrderModel.status == status)
            if table is not None:
                stmt = stmt.where(OrderModel.table_number == table)
            if since is not None:
                stmt = stmt.where(OrderModel.placed_at > parse_timestamp(since, "since"))
            rows = (await self._uow.session.execute(stmt)).scalars().all()
            return [order_model_to_domain(row) for row in rows]
