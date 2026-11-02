"""Parsing of loosely-typed query/path inputs at the use-case boundary.

Anything that arrives as a bare string (ISO timestamps, ids, bucket sizes)
is turned into a typed value here, and a malformed value becomes an
`InvalidArgumentError` (-> 422) rather than a `ValueError` escaping as 500.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from forno.domain.exceptions import InvalidArgumentError

_BUCKET_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_timestamp(value: str, field: str) -> datetime:
    """ISO 8601 -> aware datetime. A naive value is taken as UTC — the
    columns it is compared against are `timestamptz`, and asyncpg refuses
    to compare naive with aware."""
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        msg = f"{field!r} must be an ISO 8601 timestamp, got {value!r}"
        raise InvalidArgumentError(msg) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def parse_uuid(value: str, field: str) -> UUID:
    try:
        return UUID(value)
    except ValueError as exc:
        msg = f"{field!r} must be a UUID, got {value!r}"
        raise InvalidArgumentError(msg) from exc


def parse_bucket_seconds(bucket: str) -> int:
    """`"5m"` -> 300. Unit is one of s/m/h/d."""
    unit = _BUCKET_UNIT_SECONDS.get(bucket[-1:])
    try:
        amount = int(bucket[:-1])
    except ValueError:
        amount = 0
    if unit is None or amount <= 0:
        msg = f"'bucket' must look like 30s / 5m / 1h / 1d, got {bucket!r}"
        raise InvalidArgumentError(msg)
    return amount * unit
