"""Shared test fixtures for the API server.

Every test runs against an in-memory SQLite database and the configured app
with four seams overridden: the DB session, the current user, the pre-flight
service, and the ARQ pool. No network, no Redis, no Postgres.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import pytest_asyncio
from app.deps import get_arq_pool, get_current_user, get_db, get_preflight_service
from app.main import create_app
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from slopolis_core.llm.client import LlmError
from slopolis_core.preflight.models import PrReference, RepositoryRef
from slopolis_core.preflight.service import PreflightService
from slopolis_db.base import Base
from slopolis_db.models import (
    GitHubInstallation,
    Repository,
    ReviewSession,
    SessionTarget,
    User,
    Workspace,
)

# --- fakes ------------------------------------------------------------------


class FakeArqPool:
    """Records enqueued jobs instead of touching Redis."""

    def __init__(self) -> None:
        self.jobs: list[tuple[str, tuple[Any, ...]]] = []

    async def enqueue_job(self, function: str, *args: Any, **kwargs: Any) -> None:
        self.jobs.append((function, args))


class FakeGitHubClient:
    """In-memory GitHub read surface used by the repositories router."""

    def __init__(self, pulls: dict[str, list[Any]] | None = None) -> None:
        self._pulls = pulls or {}

    async def list_open_pull_requests(self, full_name: str) -> list[Any]:
        return self._pulls.get(full_name, [])


class FakeGateway:
    """In-memory ``GitHubGateway`` port for pre-flight."""

    def __init__(
        self,
        *,
        refs: dict[str, PrReference] | None = None,
        covered: list[str] | None = None,
        access: bool = True,
        files: dict[tuple[str, str], str] | None = None,
    ) -> None:
        self.refs = refs or {}
        self.covered = covered if covered is not None else ["acme/api", "acme/web"]
        self.access = access
        self.files = files or {}

    async def resolve_pr(self, url: str) -> PrReference:
        if url not in self.refs:
            raise LookupError(url)
        return self.refs[url]

    async def list_covered_repos(self) -> list[str]:
        return list(self.covered)

    async def user_has_access(
        self, repo_full_name: str, *, private: bool, user_login: str
    ) -> bool:
        return self.access

    async def read_repo_file(self, repo_full_name: str, path: str) -> str | None:
        return self.files.get((repo_full_name, path))


class FakeWorkspace:
    """In-memory ``WorkspaceConfigProvider`` for pre-flight."""

    def __init__(
        self,
        *,
        model: tuple[str, str] | None = ("claude-sonnet-4", "Anthropic"),
        credential: bool = True,
        assigned: tuple[str, str] | None = None,
    ) -> None:
        self.model = model
        self.credential = credential
        self.assigned = assigned

    async def default_model(self) -> tuple[str, str] | None:
        return self.model

    async def credential_ready(self) -> bool:
        return self.credential

    async def model_assigned(self, role: str) -> tuple[str, str] | None:
        return self.assigned if self.assigned is not None else self.model


class FakeLiveCheck:
    """Live model check that can be told to fail with a typed LLM error."""

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[str] = []

    async def check(self, model: str) -> None:
        self.calls.append(model)
        if self.fail:
            raise LlmError("model unavailable")


def make_ref(
    full_name: str, number: int = 1, *, private: bool = False
) -> PrReference:
    """Build a core PR reference for pre-flight fakes."""
    _owner, name = full_name.split("/", 1)
    return PrReference(
        url=f"https://github.com/{full_name}/pull/{number}",
        repository=RepositoryRef(
            id=f"repo-{name}",
            full_name=full_name,
            private=private,
            default_branch="main",
        ),
        number=number,
        title=f"fix: {full_name} change {number}",
    )


@dataclass
class ApiHarness:
    """A test HTTP client plus the fakes it was built with."""

    client: AsyncClient
    pool: FakeArqPool = field(default_factory=FakeArqPool)


@dataclass
class Seed:
    """Ids of the workspace, user, repository, and session seeded for a test."""

    workspace_id: uuid.UUID
    user_id: uuid.UUID
    repository_id: uuid.UUID
    session_id: uuid.UUID


# --- database ---------------------------------------------------------------


@pytest_asyncio.fixture
async def engine() -> AsyncGenerator[AsyncEngine]:
    """An in-memory SQLite engine with the full schema created."""
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """A session factory bound to the in-memory engine."""
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


@pytest_asyncio.fixture
async def seeded(
    session_factory: async_sessionmaker[AsyncSession],
) -> Seed:
    """Seed one workspace, user, repository, and session."""
    async with session_factory() as session:
        workspace, user, repository = await seed_workspace(session)
        review = await seed_session(
            session, workspace=workspace, user=user, repository=repository
        )
        return Seed(
            workspace_id=workspace.id,
            user_id=user.id,
            repository_id=repository.id,
            session_id=review.id,
        )


async def seed_workspace(session: AsyncSession) -> tuple[Workspace, User, Repository]:
    """Create a workspace, a user, and one connected repository."""
    workspace = Workspace(name="Acme", slug="acme")
    session.add(workspace)
    await session.flush()

    user = User(
        workspace_id=workspace.id,
        github_id=1001,
        handle="octocat",
        name="Mona Lisa",
        avatar_url="https://avatars.example/octocat.png",
    )
    installation = GitHubInstallation(
        workspace_id=workspace.id,
        installation_id=555,
        account_login="acme",
        account_type="Organization",
    )
    session.add_all([user, installation])
    await session.flush()

    repository = Repository(
        workspace_id=workspace.id,
        installation_id=installation.id,
        github_id=4242,
        full_name="acme/api",
        private=False,
        default_branch="main",
    )
    session.add(repository)
    await session.commit()
    return workspace, user, repository


async def seed_session(
    session: AsyncSession,
    *,
    workspace: Workspace,
    user: User,
    repository: Repository,
    number: int = 7,
    status: str = "queued",
    title: str = "Guard token refresh skew",
    tokens: int = 120,
    cost: str = "0.010000",
) -> ReviewSession:
    """Create a session plus one target and commit it."""
    review = ReviewSession(
        workspace_id=workspace.id,
        title=title,
        name=f"{repository.full_name}#{number} - Jan 1",
        prompt="Focus on security",
        status=status,
        model="claude-sonnet-4",
        provider="Anthropic",
        triggered_by_user_id=user.id,
    )
    session.add(review)
    await session.flush()
    target = SessionTarget(
        session_id=review.id,
        repository_id=repository.id,
        number=number,
        title=f"fix: change {number}",
        url=f"https://github.com/{repository.full_name}/pull/{number}",
        head_branch="fix/branch",
        status=status,
        tokens=tokens,
        cost_usd=Decimal(cost),
    )
    session.add(target)
    await session.commit()
    return review


# --- app + client -----------------------------------------------------------


@pytest_asyncio.fixture
async def build_harness(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[Callable[..., Any]]:
    """Return a factory building a harness around seeded data and overrides."""
    clients: list[AsyncClient] = []

    async def _build(
        *,
        user_id: uuid.UUID,
        gateway: FakeGateway | None = None,
        workspace: FakeWorkspace | None = None,
        live_check: FakeLiveCheck | None = None,
        github_client: FakeGitHubClient | None = None,
    ) -> ApiHarness:
        app = create_app()

        async def override_db() -> AsyncGenerator[AsyncSession]:
            async with session_factory() as session:
                try:
                    yield session
                    await session.commit()
                except Exception:
                    await session.rollback()
                    raise

        async def override_user() -> User:
            async with session_factory() as session:
                user = await session.get(User, user_id)
                assert user is not None
                return user

        pool = FakeArqPool()
        service = PreflightService(
            gateway or FakeGateway(),
            workspace or FakeWorkspace(),
            live_check or FakeLiveCheck(),
        )

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_current_user] = override_user
        app.dependency_overrides[get_preflight_service] = lambda: service
        app.dependency_overrides[get_arq_pool] = lambda: pool
        app.state.github_client = github_client
        app.state.arq_pool = pool

        client = AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        )
        clients.append(client)
        return ApiHarness(client=client, pool=pool)

    yield _build

    for client in clients:
        await client.aclose()
