"""Maps domain exceptions to RFC 9457 `application/problem+json`. See
docs/api.md "Errors". `domain/` never imports `HTTPException` — this is the
one place that translation happens.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi.responses import JSONResponse

from forno.domain.exceptions import (
    CapacityExceededError,
    ConcurrentModificationError,
    DuplicateResourceError,
    DuplicateSkuError,
    IllegalTransitionError,
    InvalidArgumentError,
    MenuItemUnavailableError,
    TableAlreadyOccupiedError,
    UnknownEntityError,
)
from forno.observability.context import get_correlation_id

if TYPE_CHECKING:
    from fastapi import FastAPI, Request

# status, URI slug, human title — see docs/api.md "Errors".
_PROBLEMS: dict[type[Exception], tuple[int, str, str]] = {
    IllegalTransitionError: (409, "illegal-transition", "Illegal state transition"),
    ConcurrentModificationError: (409, "concurrent-modification", "Concurrent modification"),
    DuplicateSkuError: (409, "duplicate-sku", "Menu item SKU already exists"),
    DuplicateResourceError: (409, "duplicate-resource", "Resource already exists"),
    CapacityExceededError: (422, "capacity-exceeded", "No capacity available"),
    MenuItemUnavailableError: (422, "menu-item-unavailable", "Menu item unavailable"),
    TableAlreadyOccupiedError: (422, "table-already-occupied", "Table already occupied"),
    InvalidArgumentError: (422, "invalid-argument", "Invalid argument"),
    UnknownEntityError: (404, "not-found", "Unknown entity"),
}


def register_exception_handlers(app: FastAPI) -> None:
    for exc_type, (status, slug, title) in _PROBLEMS.items():

        async def _handler(
            request: Request,
            exc: Exception,
            status: int = status,
            slug: str = slug,
            title: str = title,
        ) -> JSONResponse:
            request_id = get_correlation_id() or request.headers.get("X-Request-ID", "-")
            return JSONResponse(
                status_code=status,
                content={
                    "type": f"https://forno.dev/problems/{slug}",
                    "title": title,
                    "status": status,
                    "detail": str(exc),
                    "instance": request.url.path,
                    "request_id": request_id,
                },
                media_type="application/problem+json",
            )

        app.add_exception_handler(exc_type, _handler)
