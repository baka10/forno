"""Fakes for application-layer tests — no mocks, no DB, no event loop beyond
asyncio itself. See CONTRIBUTING.md "Testing strategy": "No mocks for code
you own."

`FakeOrderRepository.get()` returns an independent deep copy per call, the
way a real `SELECT` would hand back a fresh ORM object each session — that's
what makes the optimistic-locking test in test_order_use_cases.py meaningful
rather than trivially true.
"""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING, NoReturn, Self

from forno.domain.exceptions import ConcurrentModificationError, UnknownEntityError
from forno.domain.ports import EventPublisher, OrderRepository

if TYPE_CHECKING:
    from forno.domain.ids import OrderId
    from forno.domain.order import Order


class FakeOrderRepository(OrderRepository):
    def __init__(self) -> None:
        self._store: dict[OrderId, Order] = {}

    def seed(self, order: Order) -> None:
        self._store[order.id] = copy.deepcopy(order)

    async def get(self, order_id: OrderId) -> Order:
        try:
            return copy.deepcopy(self._store[order_id])
        except KeyError:
            msg = f"Order {order_id} not found"
            raise UnknownEntityError(msg) from None

    async def add(self, order: Order) -> None:
        self._store[order.id] = copy.deepcopy(order)

    async def save(self, order: Order) -> None:
        current = self._store.get(order.id)
        if current is None:
            msg = f"Order {order.id} not found"
            raise UnknownEntityError(msg)
        if current.version != order.version:
            msg = (
                f"Order {order.id} was modified concurrently "
                f"(expected version {order.version}, found {current.version})"
            )
            raise ConcurrentModificationError(msg)
        order.version += 1
        self._store[order.id] = copy.deepcopy(order)


class FakeUnitOfWork:
    """Implements the `UnitOfWork` protocol without a real DB session.

    `.session` deliberately raises if touched — any use case that needs it
    (PlaceOrder, ListOrders) isn't a fit for this fake and is covered by the
    integration/e2e suites instead, against a real Postgres.
    """

    def __init__(self, orders: OrderRepository, events: EventPublisher) -> None:
        self.orders: OrderRepository = orders
        self.events: EventPublisher = events
        self.committed = False
        self.rolled_back = False

    @property
    def session(self) -> NoReturn:
        msg = "FakeUnitOfWork has no real DB session — use a real one"
        raise NotImplementedError(msg)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        if exc_type is not None:
            await self.rollback()

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True
