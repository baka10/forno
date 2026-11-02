"""stats_orders.first_item_ready_at

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-02

Backs `forno_fire_group_spread_seconds` (CONTRIBUTING.md "Adding a metric"):
the stats projector records when the *first* item of an order went READY so
that, when the order itself goes READY, it can observe how long that first
pizza waited at the pass for its siblings. Nullable, no backfill — the
spread is only known for orders whose events were projected after this
migration, and a NULL simply means "not observed".
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "stats_orders",
        sa.Column("first_item_ready_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("stats_orders", "first_item_ready_at")
