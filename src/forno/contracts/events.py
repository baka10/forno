"""Versioned event payloads published to Kafka topics. See
docs/architecture.md "Wire contracts are not domain objects" and
CONTRIBUTING.md "Changing a wire contract":

  1. Additive changes only within a `schema_version`.
  2. Breaking change -> new version (`OrderPlacedV2` alongside `V1`);
     producers emit both during the transition.
  3. Unknown `schema_version` is never guessed at — routed to the DLQ as
     `poison`.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel


class OrderLineV1(BaseModel):
    # order_item_id lets projector-kitchen key `ticket_lines` back to the item
    # that later `/kitchen/tickets/{tid}/items/{iid}/...` commands address.
    order_item_id: UUID
    menu_item_id: UUID
    name_snapshot: str
    unit_price_snapshot: Decimal
    fire_group: int


class OrderPlacedV1(BaseModel):
    schema_version: Literal[1] = 1
    event_id: UUID
    occurred_at: datetime
    order_id: UUID
    channel: str
    items: list[OrderLineV1]
    correlation_id: str


class OrderAcceptedV1(BaseModel):
    schema_version: Literal[1] = 1
    event_id: UUID
    occurred_at: datetime
    order_id: UUID
    promised_at: datetime
    correlation_id: str


class OrderRejectedV1(BaseModel):
    schema_version: Literal[1] = 1
    event_id: UUID
    occurred_at: datetime
    order_id: UUID
    reason: str
    correlation_id: str


class OrderCancelledV1(BaseModel):
    schema_version: Literal[1] = 1
    event_id: UUID
    occurred_at: datetime
    order_id: UUID
    correlation_id: str


class OrderReadyV1(BaseModel):
    schema_version: Literal[1] = 1
    event_id: UUID
    occurred_at: datetime
    order_id: UUID
    correlation_id: str


class OrderServedV1(BaseModel):
    schema_version: Literal[1] = 1
    event_id: UUID
    occurred_at: datetime
    order_id: UUID
    correlation_id: str


class OrderClosedV1(BaseModel):
    schema_version: Literal[1] = 1
    event_id: UUID
    occurred_at: datetime
    order_id: UUID
    correlation_id: str


class OrderVoidedV1(BaseModel):
    schema_version: Literal[1] = 1
    event_id: UUID
    occurred_at: datetime
    order_id: UUID
    reason: str
    correlation_id: str


class ItemStateChangedV1(BaseModel):
    schema_version: Literal[1] = 1
    event_id: UUID
    occurred_at: datetime
    order_id: UUID
    item_id: UUID
    ticket_id: UUID | None
    status: str
    oven: str | None = None
    slot: int | None = None
    correlation_id: str


class MenuItemChangedV1(BaseModel):
    """Published on `forno.menu.v1`, which is log-compacted — a changelog of
    current state, not a time-retained history. See docs/architecture.md
    "Topics and partitioning"."""

    schema_version: Literal[1] = 1
    event_id: UUID
    occurred_at: datetime
    menu_item_id: UUID
    sku: str
    price: Decimal
    available: bool
