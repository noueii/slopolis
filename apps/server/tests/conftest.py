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
from app.deps import (
    get_arq_pool,
    get_current_user,
    get_db,
    get_optional_user,
    get_preflight_service,
)
from app.main import create_app
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from slopolis_core.github.errors import GitHubNotFoundError
from slopolis_core.github.models import AppInstallation, InstallationRepository
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

    def __init__(
        self,
        pulls: dict[str, list[Any]] | None = None,
        *,
        details: dict[tuple[str, int], Any] | None = None,
        checks: dict[tuple[str, str], list[Any]] | None = None,
    ) -> None:
        self._pulls = pulls or {}
        self._details = details or {}
        self._checks = checks or {}

    async def list_open_pull_requests(self, full_name: str) -> list[Any]:
        return self._pulls.get(full_name, [])

    async def get_pull_request(self, full_name: str, number: int) -> Any:
        """The single-pull-request read the route uses for diff size."""
        detail = self._details.get((full_name, number))
        if detail is not None:
            return detail
        for pull in self._pulls.get(full_name, []):
            if pull.number == number:
                return pull
        raise AssertionError(f"fake has no pull request {full_name}#{number}")

    async def list_check_runs(self, full_name: str, ref: str) -> list[Any]:
        return self._checks.get((full_name, ref), [])


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


class FakeAppInstallations:
    """In-memory App-JWT surface for the install flow.

    Records every call so tests can assert what the server asked GitHub for, and
    which installations it considers missing.
    """

    def __init__(
        self,
        *,
        slug: str = "slopolis-test",
        installations: dict[int, AppInstallation] | None = None,
        repositories: dict[int, list[InstallationRepository]] | None = None,
        missing: set[int] | None = None,
    ) -> None:
        self.slug = slug
        self.installations = installations or {}
        self.repositories = repositories or {}
        self.missing = missing or set()
        self.calls: list[tuple[str, int | None]] = []

    async def app_slug(self) -> str:
        self.calls.append(("app_slug", None))
        return self.slug

    async def get_installation(self, installation_id: int) -> AppInstallation:
        self.calls.append(("get_installation", installation_id))
        if installation_id in self.missing:
            raise GitHubNotFoundError(f"No installation {installation_id}")
        return self.installations[installation_id]

    async def list_repositories(
        self, installation_id: int
    ) -> list[InstallationRepository]:
        self.calls.append(("list_repositories", installation_id))
        return list(self.repositories.get(installation_id, []))


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


async def seed_solo_user(
    session: AsyncSession, *, github_id: int = 2002, handle: str = "newcomer"
) -> User:
    """Create an authenticated user who belongs to no workspace yet."""
    user = User(
        workspace_id=None,
        github_id=github_id,
        handle=handle,
        name="New Comer",
        avatar_url=None,
        is_admin=False,
    )
    session.add(user)
    await session.commit()
    return user


@pytest_asyncio.fixture
async def solo_user_id(
    session_factory: async_sessionmaker[AsyncSession],
) -> uuid.UUID:
    """Id of a signed-in user with no workspace (the onboarding case)."""
    async with session_factory() as session:
        return (await seed_solo_user(session)).id


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
        app_installations: FakeAppInstallations | None = None,
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

        async def override_optional_user() -> User | None:
            """Same identity, but `None`-able for browser-navigation routes."""
            async with session_factory() as session:
                return await session.get(User, user_id)

        pool = FakeArqPool()
        service = PreflightService(
            gateway or FakeGateway(),
            workspace or FakeWorkspace(),
            live_check or FakeLiveCheck(),
        )

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_current_user] = override_user
        app.dependency_overrides[get_optional_user] = override_optional_user
        app.dependency_overrides[get_preflight_service] = lambda: service
        app.dependency_overrides[get_arq_pool] = lambda: pool

        async def build_github_client() -> FakeGitHubClient:
            assert github_client is not None
            return github_client

        # Routes build their GitHub client per request; the harness pins the fake
        # (or leaves the factory unset, which is the unconfigured-App path).
        app.state.github_client_factory = (
            build_github_client if github_client is not None else None
        )
        app.state.app_installations = app_installations
        app.state.arq_pool = pool

        client = AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        )
        clients.append(client)
        return ApiHarness(client=client, pool=pool)

    yield _build

    for client in clients:
        await client.aclose()
