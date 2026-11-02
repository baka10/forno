"""Testcontainers: Postgres. See CONTRIBUTING.md "CI pipeline".

Bootstrap (docs/configuration.md "Bootstrap flow") re-POSTs the menu on
every simulator run, so a duplicate SKU is an expected, recurring case —
not an edge case. `CreateMenuItem` must turn the DB's unique-constraint
violation into a domain error the HTTP edge can map to 409, rather than
leaking a raw `IntegrityError` as a 500.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import delete

from forno.adapters.db.models import OvenModel, OvenSlotModel
from forno.adapters.db.unit_of_work import SqlAlchemyUnitOfWork
from forno.application.use_cases.management import CreateMenuItem, CreateStation, CreateTable
from forno.domain.exceptions import DuplicateResourceError, DuplicateSkuError

pytestmark = pytest.mark.integration


async def _create(session_factory, sku: str) -> None:
    uow = SqlAlchemyUnitOfWork(session_factory)
    use_case = CreateMenuItem(uow)
    await use_case.execute(
        sku=sku,
        name="Margherita",
        price=Decimal("32.00"),
        vat_rate=Decimal("0.08"),
        prep_seconds=120,
        bake_seconds=480,
        correlation_id="test",
    )


async def test_duplicate_sku_raises_duplicate_sku_error(session_factory) -> None:
    await _create(session_factory, sku="margherita")

    with pytest.raises(DuplicateSkuError):
        await _create(session_factory, sku="margherita")


async def test_duplicate_sku_does_not_poison_the_session(session_factory) -> None:
    await _create(session_factory, sku="diavola")
    with pytest.raises(DuplicateSkuError):
        await _create(session_factory, sku="diavola")

    # A fresh use case call reuses `session_factory` — this fails if the
    # aborted transaction from the duplicate above was never rolled back.
    await _create(session_factory, sku="calzone")


async def test_duplicate_oven_name_raises_duplicate_resource_error(session_factory) -> None:
    """Ovens/stations are keyed by a caller-chosen id (name), so bootstrap
    re-running with the same oven name collides on the primary key — same
    "already configured" case as a duplicate menu SKU."""
    # A random name, and explicit slot cleanup below: OvenInItem's capacity
    # query (tests/integration/test_oven_capacity.py) picks any free slot
    # from any oven, so a leftover free slot here would make that unrelated
    # test flaky against this file's session-scoped shared Postgres.
    oven_name = f"test-oven-{uuid4().hex[:8]}"
    use_case = CreateStation(SqlAlchemyUnitOfWork(session_factory))
    try:
        await use_case.execute(
            kind="oven", name=oven_name, slots=2, bake_seconds=480, correlation_id="test"
        )

        with pytest.raises(DuplicateResourceError):
            await use_case.execute(
                kind="oven", name=oven_name, slots=2, bake_seconds=480, correlation_id="test"
            )
    finally:
        async with session_factory() as session:
            await session.execute(delete(OvenSlotModel).where(OvenSlotModel.oven_id == oven_name))
            await session.execute(delete(OvenModel).where(OvenModel.id == oven_name))
            await session.commit()


async def test_duplicate_table_number_raises_duplicate_resource_error(session_factory) -> None:
    use_case = CreateTable(SqlAlchemyUnitOfWork(session_factory))
    await use_case.execute(number=42, seats=4, correlation_id="test")

    with pytest.raises(DuplicateResourceError):
        await use_case.execute(number=42, seats=4, correlation_id="test")
