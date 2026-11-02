"""`EventPublisher` implementations. Two real ones — see
docs/adr/0010-abstraction-budget.md.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from forno.adapters.db.models import OutboxModel
from forno.contracts import mapping
from forno.domain.events import (
    ItemStateChanged,
    OrderAccepted,
    OrderCancelled,
    OrderClosed,
    OrderPlaced,
    OrderReady,
    OrderRejected,
    OrderServed,
    OrderVoided,
)
from forno.domain.ports import EventPublisher

if TYPE_CHECKING:
    from collections.abc import Callable

    from pydantic import BaseModel
    from sqlalchemy.ext.asyncio import AsyncSession

    from forno.domain.ids import OrderId

# One row per domain event type: which topic it lands on (docs/architecture.md
# "Topics and partitioning") and how to map it to its versioned wire shape
# (docs/architecture.md "Wire contracts are not domain objects").
_ROUTES: dict[type, tuple[str, Callable[..., BaseModel]]] = {
    OrderPlaced: ("forno.orders.v1", mapping.order_placed_to_v1),
    OrderAccepted: ("forno.orders.v1", mapping.order_accepted_to_v1),
    OrderRejected: ("forno.orders.v1", mapping.order_rejected_to_v1),
    OrderCancelled: ("forno.orders.v1", mapping.order_cancelled_to_v1),
    OrderReady: ("forno.orders.v1", mapping.order_ready_to_v1),
    OrderServed: ("forno.orders.v1", mapping.order_served_to_v1),
    OrderClosed: ("forno.orders.v1", mapping.order_closed_to_v1),
    OrderVoided: ("forno.orders.v1", mapping.order_voided_to_v1),
    # Also forno.orders.v1, not forno.kitchen.v1: KitchenProjector only
    # subscribes to forno.orders.v1 (docs/architecture.md "Containers"), and
    # it is the one that turns these into ticket_lines transitions.
    ItemStateChanged: ("forno.orders.v1", mapping.item_state_changed_to_v1),
}


class OutboxPublisher(EventPublisher):
    """Writes the outbox row inside the caller's transaction. See
    docs/adr/0002-transactional-outbox.md."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def publish(self, order_id: OrderId, event: object, correlation_id: str) -> None:
        topic, mapper = _ROUTES[type(event)]
        wire_event = mapper(event, correlation_id)
        payload = wire_event.model_dump(mode="json")
        self._session.add(
            OutboxModel(
                aggregate_id=order_id,
                topic=topic,
                event_type=type(event).__name__,
                schema_version=payload["schema_version"],
                payload=payload,
                correlation_id=correlation_id,
                created_at=datetime.now(UTC),
                sent_at=None,
            )
        )


class InMemoryPublisher(EventPublisher):
    """Keeps application tests container-free. See CONTRIBUTING.md
    "Testing strategy" — "No mocks for code you own"."""

    def __init__(self) -> None:
        self.published: list[tuple[OrderId, object, str]] = []

    async def publish(self, order_id: OrderId, event: object, correlation_id: str) -> None:
        self.published.append((order_id, event, correlation_id))
