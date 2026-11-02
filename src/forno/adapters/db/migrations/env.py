"""Alembic environment. See CONTRIBUTING.md "Database changes":

- Every migration must be safe to run against a live system (rolling deploys):
  add columns nullable, backfill separately, tighten later. Never rename in one step.
- Indexes concurrently, outside a transaction.
- CI runs upgrade head -> downgrade -1 -> upgrade head against a fresh container.
"""

from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig
from typing import TYPE_CHECKING

from alembic import context
from sqlalchemy.ext.asyncio import async_engine_from_config

from forno.adapters.db.models import Base

if TYPE_CHECKING:
    from sqlalchemy import Connection

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# `alembic.ini` ships no URL — the running database is a deployment concern,
# not a checked-in one. See docs/configuration.md `FORNO_DATABASE_URL`.
database_url = os.environ.get("FORNO_DATABASE_URL")
if database_url:
    config.set_main_option("sqlalchemy.url", database_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def _run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
    )
    async with connectable.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(_run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
