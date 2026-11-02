"""Full stack: real Postgres + real Kafka, the actual FastAPI app wired up
exactly as `forno serve` would, driven by a real HTTP client. Deliberately
narrow scope — see test_order_placement.py.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from testcontainers.kafka import KafkaContainer
from testcontainers.postgres import PostgresContainer

from forno.adapters.http.app import create_app
from forno.settings import Settings

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


@pytest.fixture(scope="session")
def postgres_container():
    with PostgresContainer("postgres:16-alpine") as pg:
        yield pg


@pytest.fixture(scope="session")
def kafka_container():
    with KafkaContainer() as kafka:
        yield kafka


@pytest.fixture(scope="session")
def database_url(postgres_container: PostgresContainer) -> str:
    return postgres_container.get_connection_url().replace(
        "postgresql+psycopg2", "postgresql+asyncpg"
    )


@pytest.fixture(scope="session", autouse=True)
def _migrated(database_url: str) -> None:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")


@pytest_asyncio.fixture
async def client(
    database_url: str, kafka_container: KafkaContainer
) -> AsyncIterator[httpx.AsyncClient]:
    settings = Settings(
        database_url=database_url,
        kafka_bootstrap=kafka_container.get_bootstrap_server(),
    )
    app = create_app(settings)

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http_client:
            yield http_client
