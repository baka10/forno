"""`X-Role: manager`. See docs/api.md "Management — manager"."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from forno.adapters.db.unit_of_work import SqlAlchemyUnitOfWork
from forno.adapters.http.deps import Role, requires
from forno.adapters.http.wiring import get_uow
from forno.application.use_cases.management import CreateStation, ListStations, SetOvenActive
from forno.contracts.requests import CreateStationRequest, UpdateStationRequest
from forno.observability.context import get_correlation_id

router = APIRouter(tags=["stations"])


@router.post(
    "/stations",
    status_code=201,
    dependencies=[requires(Role.MANAGER)],
    summary="Create an oven or prep/pass station",
    description=(
        "Ovens get `slots` (concurrent bakes) and `bake_seconds`. Prep/pass "
        "stations get `slots` as their concurrent-claim capacity. This is "
        "how `KitchenState` (and therefore the `PromiseEstimator`) learns "
        "the kitchen's capacity — call it before placing orders."
    ),
)
async def create_station(
    body: CreateStationRequest, uow: SqlAlchemyUnitOfWork = Depends(get_uow)
) -> dict[str, object]:
    await CreateStation(uow).execute(
        kind=body.kind.value,
        name=body.name,
        slots=body.slots or 1,
        bake_seconds=body.bake_seconds,
        correlation_id=get_correlation_id() or "-",
    )
    return {"kind": body.kind, "name": body.name, "slots": body.slots}


@router.patch(
    "/stations/{station_id}",
    dependencies=[requires(Role.MANAGER)],
    summary="Take an oven out of service, or bring it back",
    description=(
        "`active: false` stops new pizzas going into this oven — the ones "
        "already baking finish — and removes its slots from the capacity "
        "the `PromiseEstimator` reasons over. This is what the `oven-down` "
        "scenario calls 40 minutes into service. Only ovens can be toggled."
    ),
)
async def update_station(
    station_id: str,
    body: UpdateStationRequest,
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> dict[str, object]:
    return await SetOvenActive(uow).execute(
        station_id, active=body.active, correlation_id=get_correlation_id() or "-"
    )


@router.get("/stations", summary="List stations and current oven occupancy")
async def list_stations(
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> list[dict[str, object]]:
    return await ListStations(uow).execute()
