"""Fixture for the hermetic end-to-end review-flow test."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path

import pytest_asyncio
from harness import Env, build_env


@pytest_asyncio.fixture
async def env(tmp_path: Path) -> AsyncGenerator[Env]:
    """Build one isolated app + worker environment on a temporary SQLite file."""
    built = await build_env(tmp_path)
    try:
        yield built
    finally:
        await built.aclose()
