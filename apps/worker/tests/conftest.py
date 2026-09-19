"""Pytest fixtures for the worker job tests.

Fakes and seeding helpers live in :mod:`worker_fakes` and :mod:`worker_seed`
(resolved via the repo's pyright ``extraPaths``); this module wires the
in-memory database and re-exports them for convenience. No network, no Redis.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from worker.deps import SessionFactory
from worker_fakes import (
    CATALOG_MODEL,
    CATALOG_PROVIDER,
    FINDING_PATH,
    HEAD_BRANCH,
    HEAD_SHA,
    PR_NUMBER,
    REPO_FULL_NAME,
    FakeLlm,
    FakePublisher,
    FakeReader,
)
from worker_seed import Harness, Seed, build_harness, seed_and_build, seed_graph

from slopolis_db.base import Base

__all__ = [
    "CATALOG_MODEL",
    "CATALOG_PROVIDER",
    "FINDING_PATH",
    "HEAD_BRANCH",
    "HEAD_SHA",
    "PR_NUMBER",
    "REPO_FULL_NAME",
    "FakeLlm",
    "FakePublisher",
    "FakeReader",
    "Harness",
    "Seed",
    "build_harness",
    "engine",
    "seed_and_build",
    "seed_graph",
    "session_factory",
]


@pytest.fixture
async def engine() -> AsyncGenerator[AsyncEngine]:
    db_engine = create_async_engine("sqlite+aiosqlite://")
    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield db_engine
    await db_engine.dispose()


@pytest.fixture
def session_factory(engine: AsyncEngine) -> SessionFactory:
    maker = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)

    @asynccontextmanager
    async def factory() -> AsyncGenerator[AsyncSession]:
        async with maker() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    return factory
