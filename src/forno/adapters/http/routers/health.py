"""Liveness and readiness are genuinely different checks — see docs/api.md
"Infrastructure". A liveness probe that queries the database restarts
healthy pods during a database blip and turns a degradation into an outage.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import text

router = APIRouter(tags=["health"])


@router.get(
    "/health/live",
    summary="Liveness probe",
    description="Process is up. Never touches dependencies — always `200` if the process runs.",
)
async def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get(
    "/health/ready",
    summary="Readiness probe",
    description=(
        "`200` once Postgres is reachable and the SSE fan-out consumer is "
        "running; `503` otherwise — never a queued lie. Also reports "
        "`time_scale` (ADR-0009) so a simulator can refuse a mismatch."
    ),
)
async def ready(request: Request) -> dict[str, str | int]:
    try:
        async with request.app.state.session_factory() as session:
            await session.execute(text("SELECT 1"))
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Database not reachable") from exc
    fanout_task = getattr(request.app.state, "fanout_task", None)
    if fanout_task is not None and fanout_task.done():
        raise HTTPException(status_code=503, detail="SSE fan-out consumer is not running")
    return {"status": "ok", "time_scale": request.app.state.clock.time_scale}
