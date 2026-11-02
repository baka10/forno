"""The input shape a `PromiseEstimator` reasons over. See docs/domain-model.md
"Promised time" and docs/adr/0007-cp-sat-promise-estimator-seam.md.

`NaiveEstimator` reads three fields of this and ignores the rest. Shaping the
input correctly — not declaring the Protocol — is the part that makes the
CP-SAT seam credible.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

    from forno.domain.ids import OrderItemId, OvenId, StationId
    from forno.domain.item import Priority


@dataclass(frozen=True, slots=True)
class OvenSlotState:
    slot: int
    release_time: datetime | None  # None if free right now


@dataclass(frozen=True, slots=True)
class OvenState:
    oven_id: OvenId
    bake_seconds: int
    slots: tuple[OvenSlotState, ...]


@dataclass(frozen=True, slots=True)
class StationState:
    station_id: StationId
    capacity: int
    current_claims: int


@dataclass(frozen=True, slots=True)
class QueuedItem:
    item_id: OrderItemId
    prep_seconds: int
    bake_seconds: int
    fire_group: int
    priority: Priority


@dataclass(frozen=True, slots=True)
class KitchenState:
    now: datetime
    ovens: tuple[OvenState, ...]
    stations: tuple[StationState, ...]
    queued: tuple[QueuedItem, ...]
    time_scale: int = 1
    """Domain seconds per wall-clock second (ADR-0009). Every duration an
    estimator reasons over (prep, bake, release times) is in domain seconds;
    the `promised_at` it returns is a wall-clock instant, so the ETA must be
    divided by this before it is added to `now`."""
