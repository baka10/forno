"""`X-Role: manager`. See docs/api.md "Management — manager"."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends

from forno.adapters.db.unit_of_work import SqlAlchemyUnitOfWork
from forno.adapters.http.deps import Role, requires
from forno.adapters.http.wiring import get_uow
from forno.application.use_cases.management import CreateMenuItem, ListMenuItems, UpdateMenuItem
from forno.contracts.requests import CreateMenuItemRequest, UpdateMenuItemRequest
from forno.contracts.responses import MenuItemResponse
from forno.domain.ids import MenuItemId
from forno.observability.context import get_correlation_id

router = APIRouter(tags=["menu"])


@router.post(
    "/menu/items",
    status_code=201,
    dependencies=[requires(Role.MANAGER)],
    summary="Add a menu item",
    description=(
        "Safe to re-run: a second create with the same `sku` returns `409 "
        "duplicate-sku`, which bootstrap callers treat as 'already configured'."
    ),
)
async def create_menu_item(
    body: CreateMenuItemRequest,
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> MenuItemResponse:
    menu_item_id = await CreateMenuItem(uow).execute(
        sku=body.sku,
        name=body.name,
        price=body.price,
        vat_rate=body.vat,
        prep_seconds=body.prep_seconds,
        bake_seconds=body.bake_seconds,
        correlation_id=get_correlation_id() or "-",
    )
    return MenuItemResponse(
        id=menu_item_id,
        sku=body.sku,
        name=body.name,
        price=body.price,
        vat=body.vat,
        available=True,
    )


@router.patch(
    "/menu/items/{item_id}",
    dependencies=[requires(Role.MANAGER)],
    summary="Update price / availability",
    description=(
        "Never rewrites already-placed orders — see docs/domain-model.md "
        '"Money": prices are snapshotted onto the order at placement time.'
    ),
)
async def update_menu_item(
    item_id: UUID,
    body: UpdateMenuItemRequest,
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> MenuItemResponse:
    await UpdateMenuItem(uow).execute(
        menu_item_id=MenuItemId(item_id),
        price=body.price,
        available=body.available,
        correlation_id=get_correlation_id() or "-",
    )
    rows = await ListMenuItems(uow).execute()
    row = next(r for r in rows if r["id"] == item_id)
    return MenuItemResponse(
        id=row["id"],
        sku=row["sku"],
        name=row["name"],
        price=row["price"],
        vat=row["vat_rate"],
        available=row["available"],
    )


@router.get("/menu", summary="List menu items")
async def list_menu(
    *, available_only: bool = False, uow: SqlAlchemyUnitOfWork = Depends(get_uow)
) -> list[MenuItemResponse]:
    rows = await ListMenuItems(uow).execute(available_only=available_only)
    return [
        MenuItemResponse(
            id=row["id"],
            sku=row["sku"],
            name=row["name"],
            price=row["price"],
            vat=row["vat_rate"],
            available=row["available"],
        )
        for row in rows
    ]
