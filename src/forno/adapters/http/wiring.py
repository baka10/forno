"""Per-request dependency wiring: turns `app.state` (built once at startup by
`create_app`) into the `UnitOfWork`, `Clock`, `PromiseEstimator` and
`SseBroadcaster` each use case needs. Kept out of `deps.py`, which is auth +
idempotency only.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from fastapi import Request  # noqa: TC002 - FastAPI's Depends() resolution needs this live

from forno.adapters.db.unit_of_work import SqlAlchemyUnitOfWork

if TYPE_CHECKING:
    from forno.adapters.messaging.sse_fanout import SseBroadcaster
    from forno.domain.clock import Clock
    from forno.domain.ports import PromiseEstimator


def get_uow(request: Request) -> SqlAlchemyUnitOfWork:
    return SqlAlchemyUnitOfWork(request.app.state.session_factory)


def get_clock(request: Request) -> Clock:
    return cast("Clock", request.app.state.clock)


def get_estimator(request: Request) -> PromiseEstimator:
    return cast("PromiseEstimator", request.app.state.estimator)


def get_broadcaster(request: Request) -> SseBroadcaster:
    return cast("SseBroadcaster", request.app.state.broadcaster)
