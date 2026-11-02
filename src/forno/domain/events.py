"""Domain events — plain dataclasses, not wire DTOs.

`contracts/events.py` holds the versioned Pydantic shapes actually published
to Kafka, with an explicit mapping function between the two (see
docs/architecture.md "Wire contracts are not domain objects"). Domain code
must never import `contracts/` outward; these are emitted by `application/`
use cases via `EventPublisher.publish()` and mapped to `contracts` there.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

    from forno.domain.ids import KitchenTicketId, MenuItemId, OrderId, OrderItemId, TableId
    from forno.domain.money import Money


@dataclass(frozen=True, slots=True)
class OrderPlacedLine:
    """One order line as announced on the wire. Carried on `OrderPlaced`
    rather than looked up later, because the kitchen projector builds
    `kitchen_tickets` / `ticket_lines` from this event alone — see
    docs/adr/0006-kitchen-tickets-as-projection.md."""

    item_id: OrderItemId
    menu_item_id: MenuItemId
    name_snapshot: str
    unit_price_snapshot: Money
    fire_group: int


@dataclass(frozen=True, slots=True)
class OrderPlaced:
    order_id: OrderId
    channel: str  # Order.channel.value — projector-stats needs it for per-channel metrics
    items: tuple[OrderPlacedLine, ...]
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class OrderAccepted:
    order_id: OrderId
    promised_at: datetime
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class OrderRejected:
    order_id: OrderId
    reason: str
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class OrderCancelled:
    order_id: OrderId
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class OrderReady:
    order_id: OrderId
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class OrderServed:
    order_id: OrderId
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class OrderClosed:
    order_id: OrderId
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class OrderVoided:
    order_id: OrderId
    reason: str
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class ItemStateChanged:
    order_id: OrderId
    item_id: OrderItemId
    ticket_id: KitchenTicketId | None
    status: str
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class TableSeated:
    table_id: TableId
    party_size: int
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class TableCleared:
    table_id: TableId
    occurred_at: datetime
