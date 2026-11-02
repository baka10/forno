"""Ports: Protocols the domain depends on, implemented in `adapters/`.

Every Protocol here has two real implementations or a documented seam — see
docs/adr/0010-abstraction-budget.md. Anything without that justification
should be a concrete class instead.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from datetime import datetime

    from forno.domain.ids import OrderId
    from forno.domain.kitchen_state import KitchenState
    from forno.domain.order import Order


@dataclass(frozen=True, slots=True)
class PromisedTime:
    """Value object wrapping the computed `promised_at` instant.

    Kept distinct from a bare `datetime` so a `PromiseEstimator` cannot
    accidentally return "now" or a duration. See docs/domain-model.md
    "Promised time".
    """

    at: datetime


class PromiseEstimator(Protocol):
    """The extension point the whole design is aimed at.

    `NaiveEstimator` (adapters/scheduling/naive.py) reads `now`, total bake
    seconds and free slot count. The documented, unimplemented seam is a
    CP-SAT solver — see docs/adr/0007-cp-sat-promise-estimator-seam.md.
    Selected by name at startup via `FORNO_PROMISE_ESTIMATOR`.
    """

    def promise(self, order: Order, state: KitchenState) -> PromisedTime: ...


class OrderRepository(Protocol):
    """The single guarded write path enforcing FSM invariants.

    Justified *not* as database swappability (docs/adr/0010) but as the one
    place all reads-for-mutation and writes of `Order` go through.
    """

    async def get(self, order_id: OrderId) -> Order: ...
    async def add(self, order: Order) -> None: ...
    async def save(self, order: Order) -> None:
        """Optimistic-locked update, keyed on `Order.version`."""
        ...


class EventPublisher(Protocol):
    """Publishes a domain event as part of the aggregate's transaction.

    Two real implementations: `OutboxPublisher` (adapters/messaging) writes
    the outbox row; `InMemoryPublisher` (tests) keeps application tests
    container-free. See docs/adr/0002-transactional-outbox.md.
    """

    async def publish(self, order_id: OrderId, event: object, correlation_id: str) -> None: ...
