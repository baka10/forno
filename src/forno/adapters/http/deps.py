"""Auth stand-in and idempotency dependency. See docs/api.md "Conventions"
("On authentication") and "Idempotency".

`X-API-Key` plus `X-Role`. Deliberate stand-in for real auth — swapping it
for OIDC changes this one dependency and no business logic.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

from fastapi import Depends, HTTPException, Request, Security
from fastapi.security import APIKeyHeader

from forno.adapters.db.models import IdempotencyKeyModel

if TYPE_CHECKING:
    from fastapi.params import Depends as DependsType
    from sqlalchemy.ext.asyncio import AsyncSession

# Deliberate stand-in for real auth — see docs/api.md "Conventions" ("On
# authentication"). Swapping this for OIDC changes this module, not callers.
# Public (not `_`-prefixed) so app.py can surface it in the Swagger description.
DEV_API_KEY = "dev"


class Role(StrEnum):
    MANAGER = "manager"
    CASHIER = "cashier"
    CHEF = "chef"
    WAITER = "waiter"


# `APIKeyHeader` (rather than reading `Request.headers` directly) registers
# these as OpenAPI security schemes, which is what makes Swagger UI show an
# "Authorize" button — set both once there and every subsequent try-it-out
# call on the page carries them, instead of retyping headers per request.
_api_key_scheme = APIKeyHeader(
    name="X-API-Key",
    scheme_name="ApiKey",
    auto_error=False,
    description=f'Dev stand-in — use `{DEV_API_KEY}`. See docs/api.md "Conventions".',
)
_role_scheme = APIKeyHeader(
    name="X-Role",
    scheme_name="Role",
    auto_error=False,
    description=(
        "One of: " + ", ".join(role.value for role in Role) + ". "
        "`manager` is a superset of every other role — it can call any "
        "endpoint, including cashier/chef/waiter-only ones."
    ),
)


def requires(*roles: Role) -> DependsType:
    """Usage: `@router.post(..., dependencies=[requires(Role.MANAGER)])`.

    `manager` is always allowed in addition to whatever is listed — the
    manager runs the pizzeria and can do anything a cashier, chef or
    waiter can, so every mutating endpoint effectively has two valid
    roles: its own and `manager`. See docs/api.md "Conventions".
    """

    async def _check(
        api_key: str | None = Security(_api_key_scheme),
        role: str | None = Security(_role_scheme),
    ) -> None:
        if api_key != DEV_API_KEY:
            raise HTTPException(status_code=403, detail="Missing or invalid X-API-Key")
        allowed = {r.value for r in roles} | {Role.MANAGER.value}
        if role not in allowed:
            raise HTTPException(
                status_code=403,
                detail=f"X-Role must be one of {sorted(allowed)}, got {role!r}",
            )

    return cast("DependsType", Depends(_check))


async def idempotency_key(request: Request) -> str | None:
    """Reads `Idempotency-Key`. Lookup/replay/conflict logic lives in
    middleware/dependency, not duplicated per endpoint — see docs/api.md
    "Idempotency"."""
    return request.headers.get("Idempotency-Key")


async def request_id(request: Request) -> str:
    """`X-Request-ID`, accepted or generated. See docs/observability.md
    "The pillars, wired together"."""
    return request.headers.get("X-Request-ID") or str(uuid4())


def hash_request_body(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


async def load_idempotent_response(
    session: AsyncSession, key: str, request_hash: str
) -> tuple[int, dict[str, Any]] | None:
    """Returns the stored `(status_code, body)` to replay verbatim, or `None`
    if this is a new key. Raises 409 if the key was reused with a different
    request body — see docs/api.md "Idempotency"."""
    row = await session.get(IdempotencyKeyModel, key)
    if row is None:
        return None
    if row.request_hash != request_hash:
        raise HTTPException(
            status_code=409,
            detail="Idempotency-Key was reused with a different request body",
        )
    return row.status_code, row.response_body


async def store_idempotent_response(
    session: AsyncSession, key: str, request_hash: str, status_code: int, body: dict[str, Any]
) -> None:
    session.add(
        IdempotencyKeyModel(
            key=key,
            request_hash=request_hash,
            status_code=status_code,
            response_body=body,
            created_at=datetime.now(UTC),
        )
    )
    await session.commit()
