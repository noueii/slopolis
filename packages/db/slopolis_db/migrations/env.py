"""Alembic environment — async engine, model metadata, and DATABASE_URL resolution."""

from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from slopolis_db import models
from slopolis_db.base import Base

DEFAULT_DATABASE_URL = "postgresql+asyncpg://slopolis:slopolis@localhost:5432/slopolis"

# Intentional side-effect import: registering all models on Base.metadata for autogenerate.
_ = models

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    """Resolve the migration URL from the environment, then core settings."""
    url = os.environ.get("DATABASE_URL")
    if url is not None:
        return url
    try:
        from slopolis_core.settings import get_settings
    except ModuleNotFoundError:
        return DEFAULT_DATABASE_URL
    return get_settings().database_url


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode, emitting SQL without a DBAPI connection."""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def _run_async_migrations() -> None:
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = _database_url()
    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode against an async engine."""
    asyncio.run(_run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
