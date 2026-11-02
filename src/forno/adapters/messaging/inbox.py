"""The inbox: `processed_events(event_id, consumer)` as a composite primary
key, written in the same transaction as the read-model effect. Converts
at-least-once delivery into effectively-once effects. See
docs/architecture.md "Why an inbox".
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy.dialects.postgresql import insert

from forno.adapters.db.models import ProcessedEventModel

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy import CursorResult
    from sqlalchemy.ext.asyncio import AsyncSession


async def mark_processed_or_skip(session: AsyncSession, event_id: UUID, consumer: str) -> bool:
    """Returns True if this is the first delivery (caller should apply the
    effect in the same transaction); False if it is a duplicate (caller
    should increment a counter and skip)."""
    stmt = (
        insert(ProcessedEventModel)
        .values(event_id=event_id, consumer=consumer, processed_at=datetime.now(UTC))
        .on_conflict_do_nothing(index_elements=["event_id", "consumer"])
    )
    result = cast("CursorResult[Any]", await session.execute(stmt))
    return result.rowcount > 0
