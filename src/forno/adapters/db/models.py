"""SQLAlchemy 2.0 ORM models — the concrete shape of `docs/domain-model.md`
"Aggregates", plus the reliability tables (outbox, inbox, idempotency keys)
from `docs/architecture.md` "Reliability machinery".

These are mapped to/from `forno.domain` objects by `repositories.py`; the
domain layer never imports SQLAlchemy (import-linter: `domain-is-pure`).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, ClassVar

from sqlalchemy import DateTime, ForeignKey, Numeric, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    # Every domain timestamp is tz-aware UTC (`datetime.now(timezone.utc)`);
    # map bare `datetime` annotations to `TIMESTAMPTZ` everywhere rather than
    # repeating `mapped_column(DateTime(timezone=True))` per column. Matches
    # the migration, which creates these columns `WITH TIME ZONE`.
    type_annotation_map: ClassVar[dict[type, DateTime]] = {datetime: DateTime(timezone=True)}


class MenuItemModel(Base):
    __tablename__ = "menu_items"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    sku: Mapped[str] = mapped_column(unique=True)
    name: Mapped[str]
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    vat_rate: Mapped[Decimal] = mapped_column(Numeric(4, 3))
    prep_seconds: Mapped[int]
    bake_seconds: Mapped[int]
    available: Mapped[bool] = mapped_column(default=True)


class TableModel(Base):
    __tablename__ = "tables"

    number: Mapped[int] = mapped_column(primary_key=True)
    seats: Mapped[int]
    occupied: Mapped[bool] = mapped_column(default=False)
    current_order_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("orders.id"))


class OvenModel(Base):
    __tablename__ = "ovens"

    id: Mapped[str] = mapped_column(primary_key=True)  # e.g. "deck-1"
    slots: Mapped[int]
    bake_seconds: Mapped[int]
    active: Mapped[bool] = mapped_column(default=True)


class OvenSlotModel(Base):
    __tablename__ = "oven_slots"
    __table_args__ = (UniqueConstraint("oven_id", "slot"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    oven_id: Mapped[str] = mapped_column(ForeignKey("ovens.id"))
    slot: Mapped[int]
    order_item_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("order_items.id"))
    release_time: Mapped[datetime | None]


class StationModel(Base):
    __tablename__ = "stations"

    id: Mapped[str] = mapped_column(primary_key=True)  # "prep" | "pass"
    capacity: Mapped[int]
    seconds_per_item: Mapped[int | None]


class ShiftModel(Base):
    __tablename__ = "shifts"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    cashiers: Mapped[int]
    chefs: Mapped[int]
    waiters: Mapped[int]
    started_at: Mapped[datetime]


class OrderModel(Base):
    __tablename__ = "orders"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    status: Mapped[str]
    channel: Mapped[str]
    table_number: Mapped[int | None] = mapped_column(ForeignKey("tables.number"))
    placed_at: Mapped[datetime]
    promised_at: Mapped[datetime | None]
    ready_at: Mapped[datetime | None]
    total_gross: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal(0))
    version: Mapped[int] = mapped_column(default=0)  # optimistic lock

    items: Mapped[list[OrderItemModel]] = relationship(back_populates="order")


class OrderItemModel(Base):
    __tablename__ = "order_items"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id"))
    menu_item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("menu_items.id"))
    name_snapshot: Mapped[str]
    unit_price_snapshot: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    vat_rate_snapshot: Mapped[Decimal] = mapped_column(Numeric(4, 3))
    # Snapshotted alongside price at placement — see docs/domain-model.md "Money"
    # rule 2. A later change to the menu item's bake time must not retroactively
    # move an already-placed order's fire-together math.
    prep_seconds: Mapped[int]
    bake_seconds: Mapped[int]
    fire_group: Mapped[int]
    status: Mapped[str]
    priority: Mapped[str] = mapped_column(default="NORMAL")
    remake_of: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("order_items.id"))
    # Denormalised from oven_slots for cheap reads (GetOrder, kitchen tickets);
    # oven_slots remains the source of truth for slot allocation/capacity.
    oven_id: Mapped[str | None] = mapped_column(ForeignKey("ovens.id"))
    oven_slot: Mapped[int | None]

    order: Mapped[OrderModel] = relationship(back_populates="items")


class KitchenTicketModel(Base):
    """Read model — projected from order events, never written directly by a
    command handler. See docs/adr/0006-kitchen-tickets-as-projection.md."""

    __tablename__ = "kitchen_tickets"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id"), unique=True)
    status: Mapped[str]
    created_at: Mapped[datetime]


class TicketLineModel(Base):
    __tablename__ = "ticket_lines"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    ticket_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("kitchen_tickets.id"))
    order_item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("order_items.id"))
    station: Mapped[str]
    status: Mapped[str]
    # When `status` last changed — lets the projector observe
    # `forno_item_stage_seconds` on the *next* transition. See migration 0002.
    status_changed_at: Mapped[datetime]


class StatsOrderModel(Base):
    """`projector-stats` read model: one row per order, upserted incrementally
    from order events. `GetStatsSummary`/`GetStatsTimeseries` query this, never
    `count(*)` over `orders` — see docs/api.md "Statistics"."""

    __tablename__ = "stats_orders"

    order_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    channel: Mapped[str]
    outcome: Mapped[str | None]  # served|cancelled|voided|rejected, set on terminal event
    placed_at: Mapped[datetime]
    ready_at: Mapped[datetime | None]
    promised_at: Mapped[datetime | None]
    total_gross: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal(0))
    # When the first item went READY — with `ready_at` this gives the
    # fire-together spread (`forno_fire_group_spread_seconds`).
    first_item_ready_at: Mapped[datetime | None] = mapped_column(default=None)


class StatsRemakeModel(Base):
    __tablename__ = "stats_remakes"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID]
    reason: Mapped[str]
    occurred_at: Mapped[datetime]


class OutboxModel(Base):
    """One row per domain event, written in the same transaction as the
    aggregate. See docs/adr/0002-transactional-outbox.md."""

    __tablename__ = "outbox"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    aggregate_id: Mapped[uuid.UUID]  # order_id — the partition key
    topic: Mapped[str]
    event_type: Mapped[str]
    schema_version: Mapped[int]
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    correlation_id: Mapped[str]
    created_at: Mapped[datetime]
    sent_at: Mapped[datetime | None]


class ProcessedEventModel(Base):
    """The inbox. Composite primary key converts at-least-once delivery into
    effectively-once effects. See docs/architecture.md "Why an inbox"."""

    __tablename__ = "processed_events"

    event_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    consumer: Mapped[str] = mapped_column(primary_key=True)
    processed_at: Mapped[datetime]


class IdempotencyKeyModel(Base):
    """Written in the same transaction as the state change it guards. See
    docs/api.md "Idempotency"."""

    __tablename__ = "idempotency_keys"

    key: Mapped[str] = mapped_column(primary_key=True)
    request_hash: Mapped[str]
    status_code: Mapped[int]
    response_body: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime]
