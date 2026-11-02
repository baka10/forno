"""The transaction-boundary port. Each use case runs inside exactly one
unit of work — see docs/architecture.md "Placing an order: the critical path"
("ONE transaction").
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, Self

if TYPE_CHECKING:
    from types import TracebackType

    from sqlalchemy.ext.asyncio import AsyncSession

    from forno.domain.ports import EventPublisher, OrderRepository


class UnitOfWork(Protocol):
    orders: OrderRepository
    events: EventPublisher

    @property
    def session(self) -> AsyncSession:
        """Direct DB access for read-model / management use cases that fall
        outside the `Order` aggregate (menu, tables, stations, shifts,
        kitchen-ticket reads, stats). `application` -> `adapters/db` is a
        permitted layering edge (docs/architecture.md "Layering"); this
        avoids inventing a repository per read model with one implementer
        each, which docs/adr/0010-abstraction-budget.md explicitly rejects.
        """
        ...

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...
