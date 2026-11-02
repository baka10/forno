"""See docs/api.md "Statistics". Served from `projector-stats` aggregates,
never `count(*)` over `orders`.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from forno.adapters.db.unit_of_work import SqlAlchemyUnitOfWork
from forno.adapters.http.wiring import get_uow
from forno.application.use_cases.stats import GetStatsSummary, GetStatsTimeseries
from forno.contracts.enums import StatsMetric
from forno.contracts.responses import (
    StatsSummaryResponse,
    StatsTimeseriesPoint,
    StatsTimeseriesResponse,
)

router = APIRouter(prefix="/stats", tags=["stats"])


@router.get(
    "/summary",
    summary="Covers, revenue, ready time, breach rate, remake rate",
    description="Optional `from_`/`to` are ISO-8601 timestamps bounding `placed_at`.",
)
async def stats_summary(
    from_: str | None = None,
    to: str | None = None,
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> StatsSummaryResponse:
    summary = await GetStatsSummary(uow).execute(from_, to)
    return StatsSummaryResponse(**summary)


@router.get(
    "/timeseries",
    summary="A metric bucketed over time",
    description="`bucket` is a duration like `5m`, `1h`, `1d` (unit: s/m/h/d).",
)
async def stats_timeseries(
    metric: StatsMetric,
    bucket: str = "5m",
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> StatsTimeseriesResponse:
    points = await GetStatsTimeseries(uow).execute(metric.value, bucket)
    return StatsTimeseriesResponse(
        metric=metric.value,
        points=[StatsTimeseriesPoint(**point) for point in points],
    )
