"""Async engine, session factory, and FastAPI dependency for the database."""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

DEFAULT_DATABASE_URL = "postgresql+asyncpg://slopolis:slopolis@localhost:5432/slopolis"


def _database_url() -> str:
    """Resolve ``DATABASE_URL`` from core settings, falling back to the environment.

    TODO(P0.2): drop the local fallback once ``slopolis_core.settings`` is always
    available; it exists only because core settings land in a parallel workstream.
    """
    try:
        from slopolis_core.settings import get_settings
    except ModuleNotFoundError:
        return os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)

    return get_settings().database_url


_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    """Return the process-wide async engine, creating it on first use."""
    global _engine
    if _engine is None:
        _engine = create_async_engine(_database_url(), pool_pre_ping=True)
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return the process-wide async session factory, creating it on first use."""
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            bind=get_engine(),
            expire_on_commit=False,
            autoflush=False,
        )
    return _session_factory


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession]:
    """Yield an :class:`AsyncSession`, committing on success and rolling back on error."""
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_db_session() -> AsyncGenerator[AsyncSession]:
    """FastAPI-style dependency yielding a session for the lifetime of a request."""
    async with get_session() as session:
        yield session
