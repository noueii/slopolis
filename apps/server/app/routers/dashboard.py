"""Dashboard home endpoint (spec 10.9).

Composes one response from the workspace's sessions: the scoped summary, the
currently running targets rendered as :class:`LiveSession`, and the most recent
sessions as compact history rows. Scope is a repository full name or the whole
workspace.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import DbSessionDep, WorkspaceIdDep
from app.routers._session_data import (
    elapsed_ms,
    is_live,
    load_sessions,
    repositories_for_targets,
    serialize_targets,
)
from app.schemas import (
    DashboardData,
    DashboardParams,
    DashboardSession,
    DashboardSummary,
    LiveSession,
    RepositoryRef,
)
from app.serializers import serialize_dashboard_session
from slopolis_core.domain import SessionStatus
from slopolis_db.models import Repository, ReviewSession, SessionTarget

__all__ = ["router"]

router = APIRouter(tags=["dashboard"])

_ALL_REPOS = "All repositories"

_STEPS = (
    "Fetching pull request metadata",
    "Snapshotting repository at head",
    "Chunking diff and surrounding context",
    "Scanning changed files for defects",
    "Cross-checking repository conventions",
    "Ranking and drafting findings",
    "Publishing review comments",
)


@router.get("/dashboard")
async def get_dashboard(
    db: DbSessionDep,
    workspace_id: WorkspaceIdDep,
    repo: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 12,
) -> DashboardData:
    """Return the scoped dashboard payload."""
    params = DashboardParams(repo=repo or None, limit=limit)
    scope_filter = params.repo
    sessions = await load_sessions(db, workspace_id=workspace_id)

    if scope_filter:
        sessions = await _filter_by_repo(db, sessions, scope_filter)

    sessions.sort(key=lambda session: _created(session), reverse=True)
    scope = scope_filter or _ALL_REPOS
    running_sessions = [session for session in sessions if is_live(session)]

    running: list[LiveSession] = []
    for session in running_sessions:
        running.extend(await _live_entries(db, session, scope_filter))
    running.sort(key=lambda entry: entry.started_at, reverse=True)

    recent: list[DashboardSession] = []
    for session in sessions[: params.limit]:
        targets = await serialize_targets(db, session.targets)
        recent.append(serialize_dashboard_session(session, targets=targets))

    return DashboardData(
        scope=scope,
        summary=_summary(scope, sessions, len(running)),
        running=running,
        recent=recent,
        generated_at=dt.datetime.now(dt.UTC),
    )


def _summary(
    scope: str, sessions: list[ReviewSession], running_count: int
) -> DashboardSummary:
    """Aggregate the dashboard analytics strip for the scope."""
    tokens = 0
    spend = 0.0
    for session in sessions:
        for target in session.targets:
            tokens += target.tokens
            spend += float(target.cost_usd)
    return DashboardSummary(
        scope=scope,
        total_sessions=len(sessions),
        running=running_count,
        failed=sum(1 for s in sessions if s.status == "failed"),
        spend_usd=round(spend, 2),
        tokens=tokens,
    )


async def _filter_by_repo(
    db: AsyncSession, sessions: list[ReviewSession], full_name: str
) -> list[ReviewSession]:
    """Keep only sessions that contain a target in ``full_name``."""
    repo_ids = await _repo_ids_for_full_name(db, full_name)
    return [
        session
        for session in sessions
        if any(target.repository_id in repo_ids for target in session.targets)
    ]


async def _repo_ids_for_full_name(
    db: AsyncSession, full_name: str
) -> set[uuid.UUID]:
    """Return repository ids matching ``full_name``."""
    rows = (
        await db.scalars(select(Repository.id).where(Repository.full_name == full_name))
    ).all()
    return set(rows)


async def _live_entries(
    db: AsyncSession, session: ReviewSession, scope_filter: str | None
) -> list[LiveSession]:
    """Render each live target of ``session`` as a :class:`LiveSession`."""
    repos = await repositories_for_targets(db, session.targets)
    entries: list[LiveSession] = []
    for target in session.targets:
        if target.status not in ("queued", "running"):
            continue
        repository = repos.get(target.repository_id)
        if repository is None:
            continue
        if scope_filter and repository.full_name != scope_filter:
            continue
        entries.append(_live_entry(session, target, repository))
    return entries


def _live_entry(
    session: ReviewSession, target: SessionTarget, repository: Repository
) -> LiveSession:
    """Build one live-session row from a session/target/repository triple."""
    progress = _progress(session, target)
    elapsed = elapsed_ms(session.started_at, session.created_at)
    step = _STEPS[min(len(_STEPS) - 1, (progress * len(_STEPS)) // 100)]
    return LiveSession(
        id=str(session.id),
        name=session.name,
        status=SessionStatus(session.status),
        repository=RepositoryRef(
            id=str(repository.id),
            full_name=repository.full_name,
            private=repository.private,
            default_branch=repository.default_branch,
        ),
        number=target.number,
        pr_label=f"{repository.full_name}#{target.number}",
        title=session.title,
        url=target.url,
        head_branch=target.head_branch,
        model=session.model,
        provider=session.provider,
        progress=progress,
        step=step,
        started_at=session.started_at or session.created_at,
        elapsed_ms=elapsed,
    )


def _progress(session: ReviewSession, target: SessionTarget) -> int:
    """Derive a stable 0-100 progress value for a live target."""
    done = sum(1 for item in session.targets if item.status in ("done", "failed"))
    total = max(1, len(session.targets))
    base = (done / total) * 100
    if target.status == "running":
        return min(99, int(base) + 40)
    return min(20, int(base))


def _created(session: ReviewSession) -> dt.datetime:
    """Return a timezone-aware creation timestamp (SQLite stores naive)."""
    value = session.created_at
    if value.tzinfo is None:
        return value.replace(tzinfo=dt.UTC)
    return value
