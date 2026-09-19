"""Graph seeding and harness wiring for the worker job tests.

Builds the minimal workspace -> installation -> repository -> session -> target
graph and a ``ReviewContext`` whose client factory returns the fake GitHub
clients. No network, no Redis.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession
from worker.config import WorkerConfig
from worker.deps import InstallationClients, InstallationRef, ReviewContext, SessionFactory
from worker.jobs.review_target import WorkerCtx
from worker_fakes import (
    CATALOG_MODEL,
    CATALOG_PROVIDER,
    PR_NUMBER,
    REPO_FULL_NAME,
    FakeLlm,
    FakePublisher,
    FakeReader,
)

from slopolis_core.github.auth import TokenCache
from slopolis_db.models import (
    GitHubInstallation,
    ModelAssignment,
    ModelCatalog,
    Repository,
    ReviewSession,
    SessionTarget,
    User,
    Workspace,
)

__all__ = ["Harness", "Seed", "build_harness", "seed_and_build", "seed_graph"]


@dataclass(frozen=True)
class Seed:
    """Ids of the seeded graph plus the fakes wired into the context."""

    workspace_id: uuid.UUID
    session_id: uuid.UUID
    target_id: uuid.UUID
    reader: FakeReader
    publisher: FakePublisher
    llm: FakeLlm


@dataclass(frozen=True)
class Harness:
    """A ready-to-run worker context plus its fakes and session factory."""

    ctx: WorkerCtx
    seed: Seed
    session_factory: SessionFactory


async def seed_graph(db: AsyncSession) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Create the minimal graph and return (workspace_id, session_id, target_id)."""
    workspace = Workspace(name="Acme", slug=f"acme-{uuid.uuid4().hex[:8]}")
    db.add(workspace)
    await db.flush()

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
    db.add_all([user, installation])
    await db.flush()

    repository = Repository(
        workspace_id=workspace.id,
        installation_id=installation.id,
        github_id=4242,
        full_name=REPO_FULL_NAME,
        private=True,
        default_branch="main",
    )
    db.add(repository)
    await db.flush()

    catalog = ModelCatalog(
        workspace_id=workspace.id,
        credential_id=None,
        model_id=CATALOG_MODEL,
        provider=CATALOG_PROVIDER,
        display_name="GPT-4o mini",
        source="imported",
    )
    db.add(catalog)
    await db.flush()
    db.add(
        ModelAssignment(
            workspace_id=workspace.id,
            role="review",
            model_catalog_id=catalog.id,
            model_id=None,
        )
    )

    review = ReviewSession(
        workspace_id=workspace.id,
        title="Review PR 42",
        name="pr-42",
        prompt="Focus on security",
        status="running",
        model=CATALOG_MODEL,
        provider=CATALOG_PROVIDER,
        triggered_by_user_id=user.id,
        started_at=dt.datetime.now(dt.UTC),
    )
    db.add(review)
    await db.flush()

    target = SessionTarget(
        session_id=review.id,
        repository_id=repository.id,
        number=PR_NUMBER,
        title="Add login",
        url=f"https://github.com/{REPO_FULL_NAME}/pull/{PR_NUMBER}",
        head_branch="main",
        status="queued",
    )
    db.add(target)
    await db.commit()
    return workspace.id, review.id, target.id


def build_harness(
    *,
    session_factory: SessionFactory,
    seed: Seed,
    config: WorkerConfig | None = None,
    job_try: int = 1,
) -> Harness:
    """Wire a ReviewContext whose client factory returns the seeded fakes."""

    async def client_factory(
        installation: InstallationRef, cache: TokenCache
    ) -> InstallationClients:
        return InstallationClients(reader=seed.reader, publisher=seed.publisher)

    review = ReviewContext(
        llm=seed.llm,
        config=(
            config
            if config is not None
            else WorkerConfig(WORKER_MAX_TRIES=4, WORKER_RETRY_BACKOFF_S=1)
        ),
        client_factory=client_factory,
    )
    ctx: WorkerCtx = {
        "review": review,
        "session_factory": session_factory,
        "job_try": job_try,
    }
    return Harness(ctx=ctx, seed=seed, session_factory=session_factory)


async def seed_and_build(
    session_factory: SessionFactory,
    *,
    reader: FakeReader | None = None,
    llm: FakeLlm | None = None,
    config: WorkerConfig | None = None,
    job_try: int = 1,
) -> Harness:
    """Seed the graph, then return a harness wired to the supplied fakes."""
    async with session_factory() as db:
        workspace_id, session_id, target_id = await seed_graph(db)
    seed = Seed(
        workspace_id=workspace_id,
        session_id=session_id,
        target_id=target_id,
        reader=reader if reader is not None else FakeReader(),
        publisher=FakePublisher(),
        llm=llm if llm is not None else FakeLlm([]),
    )
    return build_harness(
        session_factory=session_factory, seed=seed, config=config, job_try=job_try
    )
