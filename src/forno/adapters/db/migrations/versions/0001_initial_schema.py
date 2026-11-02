"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-07-22

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "menu_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("sku", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("price", sa.Numeric(10, 2), nullable=False),
        sa.Column("vat_rate", sa.Numeric(4, 3), nullable=False),
        sa.Column("prep_seconds", sa.Integer(), nullable=False),
        sa.Column("bake_seconds", sa.Integer(), nullable=False),
        sa.Column("available", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sku"),
    )

    op.create_table(
        "ovens",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("slots", sa.Integer(), nullable=False),
        sa.Column("bake_seconds", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "stations",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("capacity", sa.Integer(), nullable=False),
        sa.Column("seconds_per_item", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "shifts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("cashiers", sa.Integer(), nullable=False),
        sa.Column("chefs", sa.Integer(), nullable=False),
        sa.Column("waiters", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    # `tables.current_order_id` -> `orders.id` is added as a deferred FK
    # below: `orders` and `tables` reference each other, so one direction
    # must be created after both tables exist.
    op.create_table(
        "tables",
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("seats", sa.Integer(), nullable=False),
        sa.Column("occupied", sa.Boolean(), nullable=False),
        sa.Column("current_order_id", sa.Uuid(), nullable=True),
        sa.PrimaryKeyConstraint("number"),
    )

    op.create_table(
        "orders",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("channel", sa.String(), nullable=False),
        sa.Column("table_number", sa.Integer(), nullable=True),
        sa.Column("placed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("promised_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ready_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("total_gross", sa.Numeric(10, 2), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["table_number"], ["tables.number"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_foreign_key(
        "fk_tables_current_order_id_orders",
        "tables",
        "orders",
        ["current_order_id"],
        ["id"],
    )

    op.create_table(
        "order_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("menu_item_id", sa.Uuid(), nullable=False),
        sa.Column("name_snapshot", sa.String(), nullable=False),
        sa.Column("unit_price_snapshot", sa.Numeric(10, 2), nullable=False),
        sa.Column("vat_rate_snapshot", sa.Numeric(4, 3), nullable=False),
        sa.Column("prep_seconds", sa.Integer(), nullable=False),
        sa.Column("bake_seconds", sa.Integer(), nullable=False),
        sa.Column("fire_group", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("priority", sa.String(), nullable=False),
        sa.Column("remake_of", sa.Uuid(), nullable=True),
        sa.Column("oven_id", sa.String(), nullable=True),
        sa.Column("oven_slot", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"]),
        sa.ForeignKeyConstraint(["menu_item_id"], ["menu_items.id"]),
        sa.ForeignKeyConstraint(["remake_of"], ["order_items.id"]),
        sa.ForeignKeyConstraint(["oven_id"], ["ovens.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "oven_slots",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("oven_id", sa.String(), nullable=False),
        sa.Column("slot", sa.Integer(), nullable=False),
        sa.Column("order_item_id", sa.Uuid(), nullable=True),
        sa.Column("release_time", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["oven_id"], ["ovens.id"]),
        sa.ForeignKeyConstraint(["order_item_id"], ["order_items.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("oven_id", "slot"),
    )

    op.create_table(
        "kitchen_tickets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("order_id"),
    )

    op.create_table(
        "ticket_lines",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("ticket_id", sa.Uuid(), nullable=False),
        sa.Column("order_item_id", sa.Uuid(), nullable=False),
        sa.Column("station", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(["ticket_id"], ["kitchen_tickets.id"]),
        sa.ForeignKeyConstraint(["order_item_id"], ["order_items.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "stats_orders",
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("channel", sa.String(), nullable=False),
        sa.Column("outcome", sa.String(), nullable=True),
        sa.Column("placed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ready_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("promised_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("total_gross", sa.Numeric(10, 2), nullable=False),
        sa.PrimaryKeyConstraint("order_id"),
    )

    op.create_table(
        "stats_remakes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("reason", sa.String(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "outbox",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("aggregate_id", sa.Uuid(), nullable=False),
        sa.Column("topic", sa.String(), nullable=False),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column("correlation_id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    # `sent_at IS NULL` is the relay's poll predicate — see
    # docs/adr/0002-transactional-outbox.md. Partial index keeps it cheap
    # regardless of how large the (append-only, rarely-pruned) table gets.
    op.create_index(
        "ix_outbox_unsent",
        "outbox",
        ["id"],
        postgresql_where=sa.text("sent_at IS NULL"),
    )

    op.create_table(
        "processed_events",
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("consumer", sa.String(), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("event_id", "consumer"),
    )

    op.create_table(
        "idempotency_keys",
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("request_hash", sa.String(), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=False),
        sa.Column("response_body", JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )


def downgrade() -> None:
    op.drop_table("idempotency_keys")
    op.drop_table("processed_events")
    op.drop_index("ix_outbox_unsent", table_name="outbox")
    op.drop_table("outbox")
    op.drop_table("stats_remakes")
    op.drop_table("stats_orders")
    op.drop_table("ticket_lines")
    op.drop_table("kitchen_tickets")
    op.drop_table("oven_slots")
    op.drop_table("order_items")
    op.drop_constraint("fk_tables_current_order_id_orders", "tables", type_="foreignkey")
    op.drop_table("orders")
    op.drop_table("tables")
    op.drop_table("shifts")
    op.drop_table("stations")
    op.drop_table("ovens")
    op.drop_table("menu_items")
