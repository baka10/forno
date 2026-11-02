"""Domain exceptions, mapped to `application/problem+json` at the HTTP edge.

`domain/` never imports `HTTPException` — enforced by import-linter, not
discipline. See docs/api.md "Errors" and CONTRIBUTING.md "Code standards".
"""

from __future__ import annotations


class DomainError(Exception):
    """Base class for every exception the domain layer raises."""


class IllegalTransitionError(DomainError):
    """The request was well-formed; the current state forbids it. Maps to 409.

    See docs/domain-model.md "Order lifecycle" and docs/api.md "Errors".
    """


class CapacityExceededError(DomainError):
    """No oven slot / prep station capacity available. Maps to 422."""


class MenuItemUnavailableError(DomainError):
    """Referenced menu item does not exist or is marked unavailable. Maps to 422."""


class TableAlreadyOccupiedError(DomainError):
    """Maps to 422."""


class UnknownEntityError(DomainError):
    """Maps to 404."""


class InvalidArgumentError(DomainError):
    """A well-typed request carried a value the domain cannot interpret
    (malformed id, unparseable timestamp, empty order). Maps to 422."""


class ConcurrentModificationError(DomainError):
    """Optimistic-lock version mismatch on `OrderRepository.save`. Maps to 409
    — the same "well-formed, current state forbids it" family as
    IllegalTransitionError. See docs/domain-model.md aggregate `version` field."""


class DuplicateSkuError(DomainError):
    """A menu item with this SKU already exists. Maps to 409 — bootstrap
    (docs/configuration.md "Bootstrap flow") re-runs on every simulator
    start, so callers are expected to treat this as "already configured"."""


class DuplicateResourceError(DomainError):
    """An oven/station id or table number already exists. Maps to 409 — same
    "bootstrap re-runs on every start" reasoning as DuplicateSkuError, for
    the other resources ManagerActor.bootstrap() creates with a
    caller-chosen (not generated) primary key."""
