"""Testcontainers: a single real Postgres for the whole integration session.
Migrated once via the real alembic config (not `metadata.create_all`), so
these tests exercise the actual schema the app ships, not a stand-in.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from testcontainers.postgres import PostgresContainer

from forno.adapters.db.session import make_engine, make_session_factory

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(scope="session")
def postgres_container() -> Iterator[PostgresContainer]:
    with PostgresContainer("postgres:16-alpine") as pg:
        yield pg


@pytest.fixture(scope="session")
def database_url(postgres_container: PostgresContainer) -> str:
    # testcontainers hands back a psycopg2 URL; the app is asyncpg-only.
    url = postgres_container.get_connection_url()
    return url.replace("postgresql+psycopg2", "postgresql+asyncpg")


@pytest.fixture(scope="session", autouse=True)
def _migrated(database_url: str) -> None:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")


@pytest_asyncio.fixture
async def session_factory(database_url: str):
    engine = make_engine(database_url)
    factory = make_session_factory(engine)
    yield factory
    await engine.dispose()
