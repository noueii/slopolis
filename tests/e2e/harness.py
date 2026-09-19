"""Hermetic environment builder for the end-to-end review-flow test.

Runs the real FastAPI application over the ASGI transport and hands the real
``worker.jobs.review_target`` job its production context shape, all backed by a
single temporary on-disk SQLite database. Only the process boundaries are
faked: GitHub, the LLM gateway, the ARQ pool, and the publisher's network calls.

No Docker, no network, no Redis.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from app.adapters.workspace import WorkspaceConfigAdapter
from app.deps import get_arq_pool, get_current_user, get_db, get_preflight_service
from app.main import create_app
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from support import (
    FINDINGS_JSON,
    MODEL_ID,
    PROVIDER,
    REPO_FULL_NAME,
    FakeArqPool,
    FakeGitHub,
    FakeLiveCheck,
    FakeLlm,
    FakePublisher,
)
from worker.config import WorkerConfig
from worker.deps import InstallationClients, InstallationRef, ReviewContext
from worker.jobs.review_target import WorkerCtx

from slopolis_core.github.auth import TokenCache
from slopolis_core.preflight.service import PreflightService
from slopolis_db.base import Base
from slopolis_db.models import (
    GitHubInstallation,
    ModelAssignment,
    ModelCatalog,
    ProviderCredential,
    Repository,
    User,
    Workspace,
)

__all__ = ["Database", "Env", "Seed", "build_env", "seed_graph"]


class Database:
    """A temporary on-disk SQLite database shared by the API and the worker.

    A file (not ``:memory:``) is required because the API's dependency override
    and the worker's session factory are two independent connections that must
    observe the same rows.
    """

    def __init__(self, path: Path) -> None:
        self.engine: AsyncEngine = create_async_engine(f"sqlite+aiosqlite:///{path}")
        self.maker: async_sessionmaker[AsyncSession] = async_sessionmaker(
            self.engine, expire_on_commit=False, autoflush=False
        )

    async def create_schema(self) -> None:
        """Create the full ORM schema on the fresh database file."""
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def dispose(self) -> None:
        """Close the engine's connection pool."""
        await self.engine.dispose()

    @asynccontextmanager
    async def session(self) -> AsyncGenerator[AsyncSession]:
        """Yield a committing session, rolling back on error."""
        async with self.maker() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    def session_factory(self) -> AbstractAsyncContextManager[AsyncSession]:
        """Return the zero-arg session factory the worker context expects."""
        return self.session()


@dataclass(frozen=True)
class Seed:
    """Ids of the workspace graph seeded for the flow."""

    workspace_id: uuid.UUID
    user_id: uuid.UUID
    repository_id: uuid.UUID
    installation_id: uuid.UUID


async def seed_graph(session: AsyncSession) -> Seed:
    """Create the workspace, user, installation, repository, and model config."""
    workspace = Workspace(name="Acme", slug="acme")
    session.add(workspace)
    await session.flush()

    user = User(
        workspace_id=workspace.id,
        github_id=1001,
        handle="octocat",
        name="Mona Lisa",
        avatar_url=None,
    )
    installation = GitHubInstallation(
        workspace_id=workspace.id,
        installation_id=555,
        account_login="acme",
        account_type="Organization",
    )
    credential = ProviderCredential(
        workspace_id=workspace.id,
        provider=PROVIDER,
        base_url=None,
        encrypted_api_key=b"not-a-real-key",
        key_last4="eYk1",
        enabled=True,
    )
    session.add_all([user, installation, credential])
    await session.flush()

    repository = Repository(
        workspace_id=workspace.id,
        installation_id=installation.id,
        github_id=4242,
        full_name=REPO_FULL_NAME,
        private=False,
        default_branch="main",
        connected=True,
    )
    session.add(repository)
    await session.flush()

    catalog = ModelCatalog(
        workspace_id=workspace.id,
        credential_id=credential.id,
        model_id=MODEL_ID,
        provider=PROVIDER,
        display_name="GPT-4o mini",
        source="imported",
    )
    session.add(catalog)
    await session.flush()
    session.add(
        ModelAssignment(
            workspace_id=workspace.id,
            role="review",
            model_catalog_id=catalog.id,
            model_id=None,
        )
    )
    await session.commit()
    return Seed(
        workspace_id=workspace.id,
        user_id=user.id,
        repository_id=repository.id,
        installation_id=installation.id,
    )


@dataclass
class Env:
    """A built environment: the API client, the DB, the fakes, and the job ctx."""

    client: AsyncClient
    db: Database
    seed: Seed
    pool: FakeArqPool
    github: FakeGitHub
    publisher: FakePublisher
    llm: FakeLlm
    live_check: FakeLiveCheck
    ctx: WorkerCtx

    async def aclose(self) -> None:
        """Close the HTTP client and dispose the database engine."""
        await self.client.aclose()
        await self.db.dispose()


async def build_env(tmp_path: Path) -> Env:
    """Build the isolated end-to-end environment on ``tmp_path``."""
    database = Database(tmp_path / "e2e.db")
    await database.create_schema()
    async with database.maker() as session:
        seed = await seed_graph(session)

    pool = FakeArqPool()
    github = FakeGitHub()
    publisher = FakePublisher()
    llm = FakeLlm([FINDINGS_JSON])
    live_check = FakeLiveCheck()

    app = create_app()

    async def override_db() -> AsyncGenerator[AsyncSession]:
        async with database.maker() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def override_user() -> User:
        async with database.maker() as session:
            user = await session.get(User, seed.user_id)
            assert user is not None
            return user

    async def override_preflight() -> AsyncGenerator[PreflightService]:
        async with database.maker() as session:
            adapter = WorkspaceConfigAdapter(session, seed.workspace_id)
            yield PreflightService(github, adapter, live_check)

    def override_pool() -> FakeArqPool:
        return pool

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    app.dependency_overrides[get_preflight_service] = override_preflight
    app.dependency_overrides[get_arq_pool] = override_pool

    async def client_factory(
        installation: InstallationRef, cache: TokenCache
    ) -> InstallationClients:
        return InstallationClients(reader=github, publisher=publisher)

    review = ReviewContext(
        llm=llm,
        config=WorkerConfig(WORKER_MAX_TRIES=1, WORKER_RETRY_BACKOFF_S=1),
        client_factory=client_factory,
    )
    ctx: WorkerCtx = {
        "review": review,
        "session_factory": database.session_factory,
        "job_try": 1,
    }

    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")
    return Env(
        client=client,
        db=database,
        seed=seed,
        pool=pool,
        github=github,
        publisher=publisher,
        llm=llm,
        live_check=live_check,
        ctx=ctx,
    )
