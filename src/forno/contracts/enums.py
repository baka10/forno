"""Closed string vocabularies used on the wire. Declaring these as enums
(rather than bare `str`) does two things: Swagger renders them as dropdowns
instead of free-text boxes, and — for `RemakeReason` specifically — it closes
off what would otherwise be an unbounded Prometheus label (`forno_remakes_total{reason=...}`),
which docs/observability.md "Cardinality" explicitly calls out as the mistake
to avoid.

`Channel`, `OrderStatus` and `ItemStatus` already exist as domain enums and
are re-exported here rather than duplicated — see docs/domain-model.md.
"""

from __future__ import annotations

from enum import StrEnum

from forno.domain.item import ItemStatus
from forno.domain.order import Channel, OrderStatus

__all__ = [
    "Channel",
    "ItemStatus",
    "OrderStatus",
    "RemakeReason",
    "StationKind",
    "StatsMetric",
    "TicketStatus",
]


class StationKind(StrEnum):
    """`CreateStationRequest.kind`, and the station a `TicketLineResponse`
    currently sits at. See docs/api.md "Management"."""

    OVEN = "oven"
    PREP = "prep"
    PASS = "pass"  # noqa: S105 - expo "pass" station, not a credential


class RemakeReason(StrEnum):
    """See docs/observability.md `forno_remakes_total{reason}`."""

    BURNT = "burnt"
    DROPPED = "dropped"
    WRONG_ITEM = "wrong-item"


class TicketStatus(StrEnum):
    """`KitchenTicketResponse.status` — see docs/adr/0006-kitchen-tickets-as-projection.md.

    Must cover every status the kitchen projector writes
    (tests/contracts/test_ticket_status.py). `VOIDED` is kept apart from
    `CANCELLED` because the food may already exist: the order was voided after
    the kitchen started, not withdrawn before it.
    """

    OPEN = "OPEN"
    READY = "READY"
    CANCELLED = "CANCELLED"
    VOIDED = "VOIDED"


class StatsMetric(StrEnum):
    """`GET /stats/timeseries?metric=`. See docs/api.md "Statistics"."""

    ORDERS = "orders"
    REVENUE = "revenue"
    READY_SECONDS = "ready_seconds"
