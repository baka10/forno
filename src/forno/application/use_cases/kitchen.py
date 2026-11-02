"""Kitchen use cases. See docs/api.md "Kitchen — chef".

Chef commands are addressed against the ticket for client convenience but
routed internally to the `Order` aggregate, where the invariants live — the
ticket read model is never written directly. See
docs/adr/0006-kitchen-tickets-as-projection.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import select

from forno.adapters.db.models import (
    KitchenTicketModel,
    OvenModel,
    OvenSlotModel,
    StatsRemakeModel,
    TicketLineModel,
)
from forno.domain.events import ItemStateChanged, OrderReady
from forno.domain.exceptions import CapacityExceededError, UnknownEntityError
from forno.domain.ids import KitchenTicketId, OrderId, OrderItemId
from forno.domain.order import Order, OrderStatus
from forno.observability import metrics

if TYPE_CHECKING:
    from uuid import UUID

    from forno.application.unit_of_work import UnitOfWork
    from forno.domain.clock import Clock


@dataclass(slots=True)
class TicketLineView:
    order_item_id: UUID
    station: str
    status: str


@dataclass(slots=True)
class TicketView:
    id: UUID
    order_id: UUID
    status: str
    lines: list[TicketLineView]


async def _load_ticket(uow: UnitOfWork, ticket_id: KitchenTicketId) -> KitchenTicketModel:
    ticket = await uow.session.get(KitchenTicketModel, ticket_id)
    if ticket is None:
        msg = f"Kitchen ticket {ticket_id} not found"
        raise UnknownEntityError(msg)
    return ticket


async def _load_order_for_ticket(
    uow: UnitOfWork, ticket_id: KitchenTicketId
) -> tuple[KitchenTicketModel, Order]:
    ticket = await _load_ticket(uow, ticket_id)
    order = await uow.orders.get(OrderId(ticket.order_id))
    return ticket, order


async def _release_oven_slot(
    uow: UnitOfWork, item_id: OrderItemId, oven_id: str | None, oven_slot: int | None
) -> None:
    """Frees the slot `item_id` holds, if any. Checks the slot really is held
    by this item: on a retry (slot already released) the gauge must not be
    decremented a second time."""
    if oven_id is None or oven_slot is None:
        return
    slot = (
        await uow.session.execute(
            select(OvenSlotModel).where(
                OvenSlotModel.oven_id == oven_id,
                OvenSlotModel.slot == oven_slot,
                OvenSlotModel.order_item_id == item_id,
            )
        )
    ).scalar_one_or_none()
    if slot is None:
        return
    slot.order_item_id = None
    slot.release_time = None
    metrics.oven_slots_occupied.labels(oven=oven_id).dec()


class StartItem:
    """Claim at a prep station."""

    def __init__(self, uow: UnitOfWork, clock: Clock) -> None:
        self._uow = uow
        self._clock = clock

    async def execute(
        self, ticket_id: KitchenTicketId, item_id: OrderItemId, correlation_id: str
    ) -> Order:
        async with self._uow:
            ticket, order = await _load_order_for_ticket(self._uow, ticket_id)
            item = order.start_item(item_id)
            await self._uow.orders.save(order)
            await self._uow.events.publish(
                order.id,
                ItemStateChanged(
                    order_id=order.id,
                    item_id=item.id,
                    ticket_id=KitchenTicketId(ticket.id),
                    status=item.status.value,
                    occurred_at=self._clock.now(),
                ),
                correlation_id,
            )
            await self._uow.commit()
            return order


class OvenInItem:
    """Allocates an oven slot. Raises CapacityExceededError (-> 422) if none free.
    See docs/domain-model.md invariant #3 and #4."""

    def __init__(self, uow: UnitOfWork, clock: Clock) -> None:
        self._uow = uow
        self._clock = clock

    async def execute(
        self, ticket_id: KitchenTicketId, item_id: OrderItemId, correlation_id: str
    ) -> Order:
        async with self._uow:
            ticket, order = await _load_order_for_ticket(self._uow, ticket_id)
            item = order.get_item(item_id)
            # Fail on an illegal transition (409) *before* grabbing a slot, so a
            # double oven-in never reports "no capacity" (422) for an item that
            # is already baking.
            item.check_can_enter_oven()

            # Only slots of ovens that are switched on: `active=False` (the
            # `oven-down` scenario) must actually take capacity away.
            slot = (
                await self._uow.session.execute(
                    select(OvenSlotModel)
                    .join(OvenModel, OvenModel.id == OvenSlotModel.oven_id)
                    .where(OvenSlotModel.order_item_id.is_(None), OvenModel.active.is_(True))
                    .order_by(OvenSlotModel.oven_id, OvenSlotModel.slot)
                    .limit(1)
                    .with_for_update(skip_locked=True, of=OvenSlotModel)
                )
            ).scalar_one_or_none()
            if slot is None:
                msg = "No oven slot is free"
                raise CapacityExceededError(msg)

            now = self._clock.now()
            order.oven_in_item(item_id, slot.oven_id, slot.slot)
            slot.order_item_id = item.id
            # Bake time is domain seconds; the release instant is wall-clock (ADR-0009).
            slot.release_time = self._clock.after(item.bake_seconds)
            metrics.oven_slots_occupied.labels(oven=slot.oven_id).inc()

            await self._uow.orders.save(order)
            await self._uow.events.publish(
                order.id,
                ItemStateChanged(
                    order_id=order.id,
                    item_id=item.id,
                    ticket_id=KitchenTicketId(ticket.id),
                    status=item.status.value,
                    occurred_at=now,
                ),
                correlation_id,
            )
            await self._uow.commit()
            return order


class MarkItemReady:
    """Releases the oven slot, evaluates fire-together (docs/domain-model.md
    "Fire-together")."""

    def __init__(self, uow: UnitOfWork, clock: Clock) -> None:
        self._uow = uow
        self._clock = clock

    async def execute(
        self, ticket_id: KitchenTicketId, item_id: OrderItemId, correlation_id: str
    ) -> Order:
        async with self._uow:
            ticket, order = await _load_order_for_ticket(self._uow, ticket_id)
            was_ready = order.status == OrderStatus.READY
            item = order.mark_item_ready(item_id)
            await _release_oven_slot(self._uow, item.id, item.oven_id, item.oven_slot)

            now = self._clock.now()
            if order.status == OrderStatus.READY and not was_ready:
                order.ready_at = now

            await self._uow.orders.save(order)
            await self._uow.events.publish(
                order.id,
                ItemStateChanged(
                    order_id=order.id,
                    item_id=item.id,
                    ticket_id=KitchenTicketId(ticket.id),
                    status=item.status.value,
                    occurred_at=now,
                ),
                correlation_id,
            )
            if order.status == OrderStatus.READY and not was_ready:
                await self._uow.events.publish(
                    order.id, OrderReady(order_id=order.id, occurred_at=now), correlation_id
                )
            await self._uow.commit()
            return order


class ScrapItem:
    """Spawns a prioritised remake inheriting `promised_at` and `fire_group`.
    See docs/domain-model.md invariant #6."""

    def __init__(self, uow: UnitOfWork, clock: Clock) -> None:
        self._uow = uow
        self._clock = clock

    async def execute(
        self,
        ticket_id: KitchenTicketId,
        item_id: OrderItemId,
        reason: str,
        correlation_id: str,
    ) -> Order:
        async with self._uow:
            ticket, order = await _load_order_for_ticket(self._uow, ticket_id)
            item, remake = order.scrap_item(item_id, reason)
            await _release_oven_slot(self._uow, item.id, item.oven_id, item.oven_slot)

            now = self._clock.now()
            self._uow.session.add(
                StatsRemakeModel(order_id=order.id, reason=reason, occurred_at=now)
            )
            metrics.remakes_total.labels(reason=reason).inc()

            await self._uow.orders.save(order)
            await self._uow.events.publish(
                order.id,
                ItemStateChanged(
                    order_id=order.id,
                    item_id=item.id,
                    ticket_id=KitchenTicketId(ticket.id),
                    status=item.status.value,
                    occurred_at=now,
                ),
                correlation_id,
            )
            await self._uow.events.publish(
                order.id,
                ItemStateChanged(
                    order_id=order.id,
                    item_id=remake.id,
                    ticket_id=KitchenTicketId(ticket.id),
                    status=remake.status.value,
                    occurred_at=now,
                ),
                correlation_id,
            )
            await self._uow.commit()
            return order


class GetKitchenTicket:
    """Single-ticket read, used to render the response after a chef command
    mutates the `Order` aggregate that `ticket_id` projects from."""

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    async def execute(self, ticket_id: KitchenTicketId) -> TicketView:
        async with self._uow:
            ticket = await _load_ticket(self._uow, ticket_id)
            lines = (
                (
                    await self._uow.session.execute(
                        select(TicketLineModel).where(TicketLineModel.ticket_id == ticket.id)
                    )
                )
                .scalars()
                .all()
            )
            return TicketView(
                id=ticket.id,
                order_id=ticket.order_id,
                status=ticket.status,
                lines=[
                    TicketLineView(
                        order_item_id=line.order_item_id, station=line.station, status=line.status
                    )
                    for line in lines
                ],
            )


class GetKitchenTickets:
    """The KDS refresh-and-recover call. See docs/architecture.md "Streaming
    to clients" — the read model backing `Last-Event-ID` recovery."""

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    async def execute(
        self, station: str | None = None, status: str | None = None
    ) -> list[TicketView]:
        async with self._uow:
            # Oldest first: the board a chef reads is a FIFO queue.
            ticket_stmt = select(KitchenTicketModel).order_by(KitchenTicketModel.created_at)
            if status is not None:
                ticket_stmt = ticket_stmt.where(KitchenTicketModel.status == status)
            tickets = (await self._uow.session.execute(ticket_stmt)).scalars().all()

            views: list[TicketView] = []
            for ticket in tickets:
                line_stmt = select(TicketLineModel).where(TicketLineModel.ticket_id == ticket.id)
                if station is not None:
                    line_stmt = line_stmt.where(TicketLineModel.station == station)
                lines = (await self._uow.session.execute(line_stmt)).scalars().all()
                if station is not None and not lines:
                    continue
                views.append(
                    TicketView(
                        id=ticket.id,
                        order_id=ticket.order_id,
                        status=ticket.status,
                        lines=[
                            TicketLineView(
                                order_item_id=line.order_item_id,
                                station=line.station,
                                status=line.status,
                            )
                            for line in lines
                        ],
                    )
                )
            return views
