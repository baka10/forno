"""`X-Role: manager`. See docs/api.md "Management — manager"."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from forno.adapters.db.unit_of_work import SqlAlchemyUnitOfWork
from forno.adapters.http.deps import Role, requires
from forno.adapters.http.wiring import get_clock, get_uow
from forno.application.use_cases.management import CreateShift
from forno.contracts.requests import CreateShiftRequest
from forno.domain.clock import Clock
from forno.observability.context import get_correlation_id

router = APIRouter(tags=["shifts"])


@router.post(
    "/shifts",
    status_code=201,
    dependencies=[requires(Role.MANAGER)],
    summary="Record staff on duty",
    description=(
        "Records how many cashiers/chefs/waiters are working right now. "
        "**Not yet load-bearing**: it's persisted, but the default "
        "`NaiveEstimator` deliberately ignores station/staff capacity when "
        "computing `promised_at` (see adapters/scheduling/naive.py). This "
        "endpoint exists ahead of the CP-SAT estimator "
        "(docs/adr/0007-cp-sat-promise-estimator-seam.md), which will need "
        "real staffing data to reason about prep-station throughput. Safe "
        "to skip in a minimal bootstrap; harmless to call."
    ),
)
async def create_shift(
    body: CreateShiftRequest,
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
    clock: Clock = Depends(get_clock),
) -> dict[str, object]:
    await CreateShift(uow, clock).execute(
        cashiers=body.cashiers,
        chefs=body.chefs,
        waiters=body.waiters,
        correlation_id=get_correlation_id() or "-",
    )
    return {"cashiers": body.cashiers, "chefs": body.chefs, "waiters": body.waiters}
