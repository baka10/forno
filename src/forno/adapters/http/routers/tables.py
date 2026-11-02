"""`X-Role: manager` for creation; `waiter` for seat/clear. See
docs/api.md "Management" and "Service — waiter"."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from forno.adapters.db.unit_of_work import SqlAlchemyUnitOfWork
from forno.adapters.http.deps import Role, requires
from forno.adapters.http.wiring import get_uow
from forno.application.use_cases.management import CreateTable, ListTables
from forno.application.use_cases.service import ClearTable, SeatTable
from forno.contracts.requests import CreateTableRequest, SeatTableRequest
from forno.domain.ids import TableId
from forno.observability.context import get_correlation_id

router = APIRouter(tags=["tables"])


@router.post(
    "/tables", status_code=201, dependencies=[requires(Role.MANAGER)], summary="Register a table"
)
async def create_table(
    body: CreateTableRequest, uow: SqlAlchemyUnitOfWork = Depends(get_uow)
) -> dict[str, object]:
    await CreateTable(uow).execute(
        number=body.number, seats=body.seats, correlation_id=get_correlation_id() or "-"
    )
    return {"number": body.number, "seats": body.seats, "occupied": False}


@router.get("/tables", summary="List tables, occupancy and current order")
async def list_tables(uow: SqlAlchemyUnitOfWork = Depends(get_uow)) -> list[dict[str, object]]:
    return await ListTables(uow).execute()


@router.post(
    "/tables/{number}/seat",
    dependencies=[requires(Role.WAITER)],
    summary="Seat a party at a table",
    description="Returns `422` if the table is already occupied.",
)
async def seat_table(
    number: int, body: SeatTableRequest, uow: SqlAlchemyUnitOfWork = Depends(get_uow)
) -> dict[str, object]:
    await SeatTable(uow).execute(
        table_number=TableId(number),
        party_size=body.party_size,
        correlation_id=get_correlation_id() or "-",
    )
    return {"number": number, "occupied": True, "party_size": body.party_size}


@router.post(
    "/tables/{number}/clear",
    dependencies=[requires(Role.WAITER)],
    summary="Clear a table after service",
)
async def clear_table(
    number: int, uow: SqlAlchemyUnitOfWork = Depends(get_uow)
) -> dict[str, object]:
    await ClearTable(uow).execute(
        table_number=TableId(number), correlation_id=get_correlation_id() or "-"
    )
    return {"number": number, "occupied": False}
