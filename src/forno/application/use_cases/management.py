"""Management use cases. See docs/api.md "Management — manager".

These are how a pizzeria is configured. The simulator's `ManagerActor` calls
exactly these from the scenario YAML on every run — see docs/configuration.md
"Bootstrap flow". There is no separate seeding path.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from forno.adapters.db.models import (
    MenuItemModel,
    OvenModel,
    OvenSlotModel,
    ShiftModel,
    StationModel,
    TableModel,
)
from forno.domain.exceptions import DuplicateResourceError, DuplicateSkuError, UnknownEntityError
from forno.domain.ids import MenuItemId
from forno.observability import metrics

if TYPE_CHECKING:
    from decimal import Decimal

    from forno.application.unit_of_work import UnitOfWork
    from forno.domain.clock import Clock


class CreateMenuItem:
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    async def execute(  # noqa: PLR0913 - one field per menu-item column, no natural grouping
        self,
        sku: str,
        name: str,
        price: Decimal,
        vat_rate: Decimal,
        prep_seconds: int,
        bake_seconds: int,
        correlation_id: str,
    ) -> MenuItemId:
        del correlation_id  # menu changes are not propagated over Kafka in this phase
        async with self._uow:
            menu_item = MenuItemModel(
                sku=sku,
                name=name,
                price=price,
                vat_rate=vat_rate,
                prep_seconds=prep_seconds,
                bake_seconds=bake_seconds,
                available=True,
            )
            self._uow.session.add(menu_item)
            try:
                await self._uow.session.flush()
            except IntegrityError as exc:
                raise DuplicateSkuError(sku) from exc
            await self._uow.commit()
            return MenuItemId(menu_item.id)


class UpdateMenuItem:
    """Price and availability. Never rewrites placed orders — see
    docs/domain-model.md "Money"."""

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    async def execute(
        self,
        menu_item_id: MenuItemId,
        price: Decimal | None,
        *,
        available: bool | None,
        correlation_id: str,
    ) -> None:
        del correlation_id
        async with self._uow:
            menu_item = await self._uow.session.get(MenuItemModel, menu_item_id)
            if menu_item is None:
                msg = f"Menu item {menu_item_id} not found"
                raise UnknownEntityError(msg)
            if price is not None:
                menu_item.price = price
            if available is not None:
                menu_item.available = available
            await self._uow.commit()


class CreateStation:
    """Ovens and prep stations, with slot counts and bake times. Feeds
    `KitchenState` and therefore the `PromiseEstimator`."""

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    async def execute(
        self,
        kind: str,
        name: str,
        slots: int,
        bake_seconds: int | None,
        correlation_id: str,
    ) -> None:
        del correlation_id
        async with self._uow:
            if kind == "oven":
                slot_count = slots or 1
                self._uow.session.add(
                    OvenModel(
                        id=name, slots=slot_count, bake_seconds=bake_seconds or 480, active=True
                    )
                )
                for slot_number in range(1, slot_count + 1):
                    self._uow.session.add(
                        OvenSlotModel(
                            oven_id=name, slot=slot_number, order_item_id=None, release_time=None
                        )
                    )
            else:
                self._uow.session.add(
                    StationModel(id=name, capacity=slots or 1, seconds_per_item=bake_seconds)
                )
            try:
                await self._uow.session.flush()
            except IntegrityError as exc:
                msg = f"Station {name!r} already exists"
                raise DuplicateResourceError(msg) from exc
            if kind == "oven":
                metrics.oven_slots_total.labels(oven=name).set(slot_count)
                metrics.oven_slots_occupied.labels(oven=name).set(0)
            await self._uow.commit()


class CreateTable:
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    async def execute(self, number: int, seats: int, correlation_id: str) -> None:
        del correlation_id
        async with self._uow:
            self._uow.session.add(TableModel(number=number, seats=seats, occupied=False))
            try:
                await self._uow.session.flush()
            except IntegrityError as exc:
                msg = f"Table {number} already exists"
                raise DuplicateResourceError(msg) from exc
            await self._uow.commit()


class ListMenuItems:
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    async def execute(self, *, available_only: bool = False) -> list[dict[str, object]]:
        async with self._uow:
            stmt = select(MenuItemModel)
            if available_only:
                stmt = stmt.where(MenuItemModel.available.is_(True))
            rows = (await self._uow.session.execute(stmt)).scalars().all()
            return [
                {
                    "id": row.id,
                    "sku": row.sku,
                    "name": row.name,
                    "price": row.price,
                    "vat_rate": row.vat_rate,
                    "available": row.available,
                }
                for row in rows
            ]


class SetOvenActive:
    """Take an oven out of service (or bring it back). Pizzas already in it
    finish baking; no new pizza is put in while it is inactive — `OvenInItem`
    only allocates slots of active ovens, and the `PromiseEstimator` only
    counts them. This is the `oven-down` scenario's lever."""

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    async def execute(
        self, oven_id: str, *, active: bool, correlation_id: str
    ) -> dict[str, object]:
        del correlation_id
        async with self._uow:
            oven = await self._uow.session.get(OvenModel, oven_id)
            if oven is None:
                msg = f"Oven {oven_id!r} not found"
                raise UnknownEntityError(msg)
            oven.active = active
            # Configured capacity, as the dashboard sees it: an oven that is
            # off contributes no slots to "occupied / total".
            metrics.oven_slots_total.labels(oven=oven.id).set(oven.slots if active else 0)
            await self._uow.commit()
            return {"id": oven.id, "kind": "oven", "slots": oven.slots, "active": oven.active}


class ListStations:
    """Ovens and prep/pass stations together, with current oven occupancy —
    docs/api.md "GET /stations": "Current occupancy"."""

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    async def execute(self) -> list[dict[str, object]]:
        async with self._uow:
            ovens = (await self._uow.session.execute(select(OvenModel))).scalars().all()
            views = []
            for oven in ovens:
                occupied = (
                    await self._uow.session.execute(
                        select(func.count()).where(
                            OvenSlotModel.oven_id == oven.id,
                            OvenSlotModel.order_item_id.is_not(None),
                        )
                    )
                ).scalar_one()
                views.append(
                    {
                        "id": oven.id,
                        "kind": "oven",
                        "slots": oven.slots,
                        "occupied": occupied,
                        "bake_seconds": oven.bake_seconds,
                        "active": oven.active,
                    }
                )
            stations = (await self._uow.session.execute(select(StationModel))).scalars().all()
            views.extend(
                {
                    "id": station.id,
                    "kind": station.id,
                    "capacity": station.capacity,
                    "seconds_per_item": station.seconds_per_item,
                }
                for station in stations
            )
            return views


class ListTables:
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    async def execute(self) -> list[dict[str, object]]:
        async with self._uow:
            rows = (await self._uow.session.execute(select(TableModel))).scalars().all()
            return [
                {
                    "number": row.number,
                    "seats": row.seats,
                    "occupied": row.occupied,
                    "current_order_id": row.current_order_id,
                }
                for row in rows
            ]


class CreateShift:
    """Staff on shift — feeds capacity in `KitchenState`."""

    def __init__(self, uow: UnitOfWork, clock: Clock) -> None:
        self._uow = uow
        self._clock = clock

    async def execute(
        self,
        cashiers: int,
        chefs: int,
        waiters: int,
        correlation_id: str,
    ) -> None:
        del correlation_id
        async with self._uow:
            self._uow.session.add(
                ShiftModel(
                    cashiers=cashiers,
                    chefs=chefs,
                    waiters=waiters,
                    started_at=self._clock.now(),
                )
            )
            await self._uow.commit()
