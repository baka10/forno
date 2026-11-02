"""`X-Role: chef` (manager can also do everything chef can, see
`deps.requires`). See docs/api.md "Kitchen — chef".

Commands are addressed against the ticket for client convenience but routed
internally to the order aggregate. See
docs/adr/0006-kitchen-tickets-as-projection.md.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends

from forno.adapters.db.unit_of_work import SqlAlchemyUnitOfWork
from forno.adapters.http.deps import Role, requires
from forno.adapters.http.wiring import get_clock, get_uow
from forno.application.use_cases.kitchen import (
    GetKitchenTicket,
    GetKitchenTickets,
    MarkItemReady,
    OvenInItem,
    ScrapItem,
    StartItem,
    TicketView,
)
from forno.contracts.enums import StationKind, TicketStatus
from forno.contracts.requests import ScrapItemRequest
from forno.contracts.responses import KitchenTicketResponse, TicketLineResponse
from forno.domain.clock import Clock
from forno.domain.ids import KitchenTicketId, OrderItemId
from forno.observability.context import get_correlation_id

router = APIRouter(prefix="/kitchen", tags=["kitchen"])


def _to_response(view: TicketView) -> KitchenTicketResponse:
    return KitchenTicketResponse(
        id=view.id,
        order_id=view.order_id,
        status=view.status,
        lines=[
            TicketLineResponse(
                order_item_id=line.order_item_id, station=line.station, status=line.status
            )
            for line in view.lines
        ],
    )


@router.get(
    "/tickets",
    summary="List kitchen tickets",
    description=(
        "The KDS refresh-and-recover call: a client that missed SSE events "
        "(see `/streams/kitchen`) rebuilds its view from here. Eventually "
        "consistent with the order aggregate — projected asynchronously off "
        "Kafka, typically within milliseconds. No role required."
    ),
)
async def list_tickets(
    station: StationKind | None = None,
    status: TicketStatus | None = None,
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> list[KitchenTicketResponse]:
    views = await GetKitchenTickets(uow).execute(
        station=station.value if station else None,
        status=status.value if status else None,
    )
    return [_to_response(view) for view in views]


@router.post(
    "/tickets/{ticket_id}/items/{item_id}/start",
    dependencies=[requires(Role.CHEF)],
    summary="Claim an item at a prep station",
)
async def start_item(
    ticket_id: UUID,
    item_id: UUID,
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
    clock: Clock = Depends(get_clock),
) -> KitchenTicketResponse:
    await StartItem(uow, clock).execute(
        KitchenTicketId(ticket_id), OrderItemId(item_id), get_correlation_id() or "-"
    )
    return _to_response(await GetKitchenTicket(uow).execute(KitchenTicketId(ticket_id)))


@router.post(
    "/tickets/{ticket_id}/items/{item_id}/oven-in",
    dependencies=[requires(Role.CHEF)],
    summary="Put an item in the oven",
    description="Allocates an oven slot. Returns `422` if none is free.",
)
async def oven_in_item(
    ticket_id: UUID,
    item_id: UUID,
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
    clock: Clock = Depends(get_clock),
) -> KitchenTicketResponse:
    await OvenInItem(uow, clock).execute(
        KitchenTicketId(ticket_id), OrderItemId(item_id), get_correlation_id() or "-"
    )
    return _to_response(await GetKitchenTicket(uow).execute(KitchenTicketId(ticket_id)))


@router.post(
    "/tickets/{ticket_id}/items/{item_id}/ready",
    dependencies=[requires(Role.CHEF)],
    summary="Mark an item ready / pull from the oven",
    description=(
        "Releases the oven slot and evaluates fire-together: the order "
        "reaches `READY` once every one of its items is READY or DELIVERED. "
        'See docs/domain-model.md "Fire-together".'
    ),
)
async def item_ready(
    ticket_id: UUID,
    item_id: UUID,
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
    clock: Clock = Depends(get_clock),
) -> KitchenTicketResponse:
    await MarkItemReady(uow, clock).execute(
        KitchenTicketId(ticket_id), OrderItemId(item_id), get_correlation_id() or "-"
    )
    return _to_response(await GetKitchenTicket(uow).execute(KitchenTicketId(ticket_id)))


@router.post(
    "/tickets/{ticket_id}/items/{item_id}/scrap",
    dependencies=[requires(Role.CHEF)],
    summary="Scrap an item (burnt / dropped / wrong item)",
    description=(
        "Spawns a prioritised remake that inherits the original item's "
        "`promised_at` and `fire_group` — the remake shows up in the "
        "promise-breach SLI, exactly where a burnt pizza belongs. See "
        "docs/domain-model.md invariant #6."
    ),
)
async def scrap_item(
    ticket_id: UUID,
    item_id: UUID,
    body: ScrapItemRequest,
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
    clock: Clock = Depends(get_clock),
) -> KitchenTicketResponse:
    await ScrapItem(uow, clock).execute(
        KitchenTicketId(ticket_id),
        OrderItemId(item_id),
        body.reason.value,
        get_correlation_id() or "-",
    )
    return _to_response(await GetKitchenTicket(uow).execute(KitchenTicketId(ticket_id)))
