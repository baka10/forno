"""Front-of-house use cases. See docs/api.md "Service — waiter"."""

from __future__ import annotations

from typing import TYPE_CHECKING

from forno.adapters.db.models import TableModel
from forno.domain.exceptions import TableAlreadyOccupiedError, UnknownEntityError

if TYPE_CHECKING:
    from forno.application.unit_of_work import UnitOfWork
    from forno.domain.ids import TableId


class SeatTable:
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    async def execute(self, table_number: TableId, party_size: int, correlation_id: str) -> None:
        del correlation_id, party_size  # not modelled on `tables`; validated only
        async with self._uow:
            table = await self._uow.session.get(TableModel, table_number)
            if table is None:
                msg = f"Table {table_number} not found"
                raise UnknownEntityError(msg)
            if table.occupied:
                msg = f"Table {table_number} is already occupied"
                raise TableAlreadyOccupiedError(msg)
            table.occupied = True
            await self._uow.commit()


class ClearTable:
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    async def execute(self, table_number: TableId, correlation_id: str) -> None:
        del correlation_id
        async with self._uow:
            table = await self._uow.session.get(TableModel, table_number)
            if table is None:
                msg = f"Table {table_number} not found"
                raise UnknownEntityError(msg)
            table.occupied = False
            table.current_order_id = None
            await self._uow.commit()
