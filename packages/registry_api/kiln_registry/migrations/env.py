"""Alembic environment for kiln_registry.

Resolves the database URL from the same place the app does
(``kiln_registry.db._get_database_url``) so migrations and runtime never
drift apart. Works against both async SQLite (local dev) and
asyncpg-backed PostgreSQL (prod) via ``async_engine_from_config``.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from kiln_registry.db import Base, _get_database_url

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Alembic parses this value through ConfigParser, which treats `%` as a
# formatter token. Escape any `%` (e.g. percent-encoded chars in URL creds)
# so DATABASE_URLs like `postgresql://u:p%40ss@host/db` don't blow up.
config.set_main_option("sqlalchemy.url", _get_database_url().replace("%", "%%"))

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


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
