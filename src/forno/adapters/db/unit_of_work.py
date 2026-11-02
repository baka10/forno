"""`UnitOfWork` implementation over an `AsyncSession`. See
docs/application/unit_of_work.py (the port) and docs/architecture.md
"Placing an order: the critical path" — aggregate + outbox in one transaction.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Self

from forno.adapters.db.repositories import SqlAlchemyOrderRepository
from forno.adapters.messaging.publisher import OutboxPublisher
from forno.application.unit_of_work import UnitOfWork

if TYPE_CHECKING:
    from types import TracebackType

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


class SqlAlchemyUnitOfWork(UnitOfWork):
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self._session: AsyncSession | None = None

    @property
    def session(self) -> AsyncSession:
        if self._session is None:
            msg = "UnitOfWork used outside `async with`"
            raise RuntimeError(msg)
        return self._session

    async def __aenter__(self) -> Self:
        self._session = self._session_factory()
        self.orders = SqlAlchemyOrderRepository(self._session)
        self.events = OutboxPublisher(self._session)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        try:
            if exc_type is not None:
                await self.rollback()
        finally:
            await self.session.close()
            self._session = None

    async def commit(self) -> None:
        await self.session.commit()

    async def rollback(self) -> None:
        await self.session.rollback()
