"""Statistics use cases. See docs/api.md "Statistics".

Served from aggregates maintained incrementally by `projector-stats`, never
`count(*)` over `orders` — the whole reason a second projector exists.
Computing them on read would fall over exactly when someone opens the
dashboard during the rush.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import func, select

from forno.adapters.db.models import StatsOrderModel, StatsRemakeModel
from forno.application.parsing import parse_bucket_seconds, parse_timestamp

if TYPE_CHECKING:
    from forno.application.unit_of_work import UnitOfWork


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


class GetStatsSummary:
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    async def execute(self, from_: str | None, to: str | None) -> dict[str, object]:
        since = parse_timestamp(from_, "from") if from_ is not None else None
        until = parse_timestamp(to, "to") if to is not None else None

        async with self._uow:
            stmt = select(StatsOrderModel)
            if since is not None:
                stmt = stmt.where(StatsOrderModel.placed_at >= since)
            if until is not None:
                stmt = stmt.where(StatsOrderModel.placed_at <= until)
            rows = (await self._uow.session.execute(stmt)).scalars().all()

            served = [row for row in rows if row.outcome == "served"]
            covers = len(served)
            revenue = sum((row.total_gross for row in served), Decimal(0))

            # Rates share one denominator: orders that actually reached READY.
            # Dividing late-readies by *closed* orders (a different, smaller
            # set) produced breach rates above 1.0 whenever orders were still
            # open or got voided.
            readied = [
                (row.ready_at, row.placed_at, row.promised_at)
                for row in rows
                if row.ready_at is not None
            ]
            ready_seconds = [(ready - placed).total_seconds() for ready, placed, _ in readied]
            breaches = sum(
                1 for ready, _, promised in readied if promised is not None and ready > promised
            )

            remake_stmt = select(func.count()).select_from(StatsRemakeModel)
            if since is not None:
                remake_stmt = remake_stmt.where(StatsRemakeModel.occurred_at >= since)
            if until is not None:
                remake_stmt = remake_stmt.where(StatsRemakeModel.occurred_at <= until)
            remakes = (await self._uow.session.execute(remake_stmt)).scalar_one()

            return {
                "covers": covers,
                "revenue": revenue,
                "median_ready_seconds": _median(ready_seconds),
                "breach_rate": (breaches / len(readied)) if readied else 0.0,
                "remake_rate": (remakes / len(rows)) if rows else 0.0,
            }


class GetStatsTimeseries:
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    async def execute(self, metric: str, bucket: str = "5m") -> list[dict[str, object]]:
        bucket_s = parse_bucket_seconds(bucket)
        async with self._uow:
            rows = (await self._uow.session.execute(select(StatsOrderModel))).scalars().all()

        buckets: dict[datetime, list[StatsOrderModel]] = {}
        for row in rows:
            epoch = int(row.placed_at.timestamp())
            bucket_start = datetime.fromtimestamp((epoch // bucket_s) * bucket_s, tz=UTC)
            buckets.setdefault(bucket_start, []).append(row)

        points: list[dict[str, object]] = []
        for bucket_start in sorted(buckets):
            group = buckets[bucket_start]
            if metric == "orders":
                value = float(len(group))
            elif metric == "revenue":
                value = float(sum((row.total_gross for row in group), Decimal(0)))
            elif metric == "ready_seconds":
                readies = [
                    (row.ready_at - row.placed_at).total_seconds() for row in group if row.ready_at
                ]
                value = (sum(readies) / len(readies)) if readies else 0.0
            else:
                value = 0.0
            points.append({"bucket_start": bucket_start, "value": value})
        return points
