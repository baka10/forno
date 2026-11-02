"""ticket_lines.status_changed_at

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-26

Backs `forno_item_stage_seconds` (CONTRIBUTING.md "Adding a metric") — the
projector needs to know when a line entered its *current* status to observe
how long it spent there on the next transition. Added nullable, backfilled,
then tightened to NOT NULL in the same migration: `ticket_lines` is a small,
per-ticket read model (not the high-volume `orders` table CONTRIBUTING.md's
rolling-deploy guidance is really guarding), so a single migration doing all
three steps doesn't hold a lock for a service-affecting amount of time.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ticket_lines",
        sa.Column("status_changed_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Backfill from the parent ticket's creation time — the best available
    # approximation for existing rows; `forno_item_stage_seconds` will only
    # be accurate for lines created after this migration.
    op.execute(
        """
        UPDATE ticket_lines
        SET status_changed_at = kitchen_tickets.created_at
        FROM kitchen_tickets
        WHERE ticket_lines.ticket_id = kitchen_tickets.id
        """
    )
    op.alter_column("ticket_lines", "status_changed_at", nullable=False)


def downgrade() -> None:
    op.drop_column("ticket_lines", "status_changed_at")
