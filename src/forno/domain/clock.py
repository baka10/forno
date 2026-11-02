"""The `Clock` port. Three implementations — see docs/adr/0010-abstraction-budget.md
and docs/adr/0009-scaled-domain-time.md.

`SystemClock` and `FrozenClock` are pure stdlib and live here. `ScaledClock`
(domain durations divided by `time_scale`, wall clock untouched) belongs to
whichever process needs simulated time — see docs/simulator.md "Time". The
simulator's copy must not import this module (import-linter: simulator-is-external);
it implements the same shape independently.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol


class Clock(Protocol):
    """`now()` is always wall-clock. `time_scale` says how many *domain*
    seconds elapse per wall-clock second (ADR-0009): a bake of 480 domain
    seconds at `time_scale=120` occupies the oven for 4 wall seconds, and a
    promise or slot release computed from domain durations has to be
    shortened by the same factor or it is made in the wrong unit."""

    @property
    def time_scale(self) -> int: ...

    def now(self) -> datetime: ...

    def after(self, domain_seconds: float) -> datetime:
        """`now()` plus a domain duration, converted to wall-clock."""
        ...


class SystemClock:
    """Wall-clock time, `Clock` implementation used by the running application.
    `time_scale` comes from `FORNO_TIME_SCALE` (1 outside the simulator)."""

    def __init__(self, time_scale: int = 1) -> None:
        if time_scale < 1:
            msg = f"time_scale must be >= 1, got {time_scale}"
            raise ValueError(msg)
        self._time_scale = time_scale

    @property
    def time_scale(self) -> int:
        return self._time_scale

    def now(self) -> datetime:
        return datetime.now(UTC)

    def after(self, domain_seconds: float) -> datetime:
        return self.now() + timedelta(seconds=domain_seconds / self._time_scale)


class FrozenClock:
    """Fixed instant. Makes promise-breach logic testable without `datetime.now()`.

    See docs/domain-model.md "Promised time" and CONTRIBUTING.md "Testing strategy".
    """

    def __init__(self, at: datetime, time_scale: int = 1) -> None:
        self._at = at
        self._time_scale = time_scale

    @property
    def time_scale(self) -> int:
        return self._time_scale

    def now(self) -> datetime:
        return self._at

    def after(self, domain_seconds: float) -> datetime:
        return self._at + timedelta(seconds=domain_seconds / self._time_scale)

    def advance(self, seconds: float) -> None:
        self._at = self._at + timedelta(seconds=seconds)
