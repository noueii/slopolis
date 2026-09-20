"""Shared session-persistence helpers (queries, aggregates, live projection).

Sessions are loaded with their targets, repositories, findings, and triggering
user in one round trip. This module owns the eager-loading strategy and the
per-target aggregate computation so the sessions and dashboard routers stay
focused on shaping responses.

It also owns the per-viewer read filter (spec 10.8 §Access): workspace membership
does not imply repository access, so every session read narrows its targets to the
repositories the *viewer* can read. The rule lives here, once, so the session
list, the dashboard, the session detail, and the run-tree endpoints cannot drift
apart on who may see what.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.errors import ApiError
from app.schemas import SessionTarget as SessionTargetSchema
from app.serializers import serialize_target
from app.services.repo_access import RepoAccessChecker
from slopolis_db.models import (
    Finding,
    Repository,
    ReviewSession,
    SessionTarget,
    User,
)

__all__ = [
    "TargetAccess",
    "accessible_views",
    "finding_counts_for",
    "load_session",
    "load_sessions",
    "repositories_for_targets",
    "require_session_access",
    "session_query",
]

_LIVE_STATUSES = ("queued", "running")


def session_query() -> Select[tuple[ReviewSession]]:
    """Return the base session query with every relation eager-loaded."""
    return select(ReviewSession).options(
        selectinload(ReviewSession.targets),
    )


async def load_sessions(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    session_ids: list[uuid.UUID] | None = None,
) -> list[ReviewSession]:
    """Load sessions (optionally a subset) with their targets ordered."""
    query = session_query().where(ReviewSession.workspace_id == workspace_id)
    if session_ids is not None:
        if not session_ids:
            return []
        query = query.where(ReviewSession.id.in_(session_ids))
    rows = list((await db.scalars(query)).all())
    for row in rows:
        row.targets.sort(key=lambda target: (target.created_at, target.number))
    return rows


async def load_session(
    db: AsyncSession, session_id: uuid.UUID, *, workspace_id: uuid.UUID
) -> ReviewSession | None:
    """Load one session with its targets, or ``None`` when absent."""
    row = await db.scalar(
        session_query().where(
            ReviewSession.id == session_id,
            ReviewSession.workspace_id == workspace_id,
        )
    )
    if row is not None:
        row.targets.sort(key=lambda target: (target.created_at, target.number))
    return row


@dataclass(frozen=True)
class TargetAccess:
    """One viewer's verdict on a session: what they may read, and how sure we are.

    ``unverifiable`` means at least one target's repository could not be checked
    (no token, revoked token, GitHub unreachable), so an empty ``targets`` may
    hide content rather than prove there is none.
    """

    targets: list[SessionTarget]
    unverifiable: bool


async def accessible_views(
    db: AsyncSession,
    sessions: list[ReviewSession],
    *,
    viewer: User,
    checker: RepoAccessChecker,
) -> dict[uuid.UUID, TargetAccess]:
    """Return, per session, the targets ``viewer`` may read (spec 10.8 §Access).

    The triggering user keeps their whole session: pre-flight already verified
    their access at submit time, so showing them their own review costs no GitHub
    round trip. Everything else is one batched check per distinct repository
    across the whole list, so a page costs a call per repository, not per row.
    """
    views: dict[uuid.UUID, TargetAccess] = {}
    pending: list[ReviewSession] = []
    for session in sessions:
        if session.triggered_by_user_id == viewer.id:
            views[session.id] = TargetAccess(
                targets=list(session.targets), unverifiable=False
            )
        else:
            pending.append(session)
    if not pending:
        return views

    targets = [target for session in pending for target in session.targets]
    repositories = await repositories_for_targets(db, targets)
    full_names = [
        repositories[target.repository_id].full_name
        for target in targets
        if target.repository_id in repositories
    ]
    verdicts = await checker.can_read_many(viewer, full_names)

    for session in pending:
        kept: list[SessionTarget] = []
        unverifiable = False
        for target in session.targets:
            repository = repositories.get(target.repository_id)
            if repository is None:
                # A target whose repository row is gone cannot be serialized at
                # all, so it is not something this viewer is losing.
                continue
            verdict = verdicts.get(repository.full_name)
            if verdict is True:
                kept.append(target)
            elif verdict is None:
                unverifiable = True
        views[session.id] = TargetAccess(targets=kept, unverifiable=unverifiable)
    return views


async def require_session_access(
    db: AsyncSession,
    session: ReviewSession,
    *,
    viewer: User,
    checker: RepoAccessChecker,
) -> TargetAccess:
    """Return the viewer's view of one session, or raise the refusal for it.

    Checks that ran and answered "no" leave the session indistinguishable from an
    absent one (404); checks that could not run at all are reported as such
    (403 ``repo_access_unverified``) rather than quietly showing less.
    """
    views = await accessible_views(db, [session], viewer=viewer, checker=checker)
    view = views[session.id]
    if view.targets:
        return view
    if view.unverifiable:
        raise ApiError(
            403,
            "repo_access_unverified",
            "Your GitHub access to this session's repositories could not be verified.",
        )
    raise ApiError(
        404,
        "session_not_found",
        "That review session does not exist.",
        detail=f"No session with id {session.id}.",
    )


async def finding_counts_for(
    db: AsyncSession, target_ids: list[uuid.UUID]
) -> dict[uuid.UUID, int]:
    """Return finding counts keyed by target id."""
    if not target_ids:
        return {}
    rows = (
        await db.execute(
            select(Finding.target_id, func.count(Finding.id))
            .where(Finding.target_id.in_(target_ids))
            .group_by(Finding.target_id)
        )
    ).all()
    return {target_id: count for target_id, count in rows}


async def repositories_for_targets(
    db: AsyncSession, targets: list[SessionTarget]
) -> dict[uuid.UUID, Repository]:
    """Return repository rows keyed by id for the given targets."""
    repo_ids = {target.repository_id for target in targets}
    if not repo_ids:
        return {}
    rows = list(
        (
            await db.scalars(select(Repository).where(Repository.id.in_(repo_ids)))
        ).all()
    )
    return {row.id: row for row in rows}


async def serialize_targets(
    db: AsyncSession, targets: list[SessionTarget]
) -> list[SessionTargetSchema]:
    """Serialize a session's targets with findings counts and repo refs."""
    counts = await finding_counts_for(db, [target.id for target in targets])
    repos = await repositories_for_targets(db, targets)
    result: list[SessionTargetSchema] = []
    for target in targets:
        repository = repos.get(target.repository_id)
        if repository is None:
            continue
        result.append(
            serialize_target(target, counts.get(target.id, 0), repository)
        )
    return result


def is_live(session: ReviewSession) -> bool:
    """Return whether a session is still queued or running."""
    return session.status in _LIVE_STATUSES


def elapsed_ms(started_at: dt.datetime | None, created_at: dt.datetime) -> int:
    """Return elapsed wall-clock milliseconds since the session started."""
    start = started_at or created_at
    return max(0, int((dt.datetime.now(dt.UTC) - _aware(start)).total_seconds() * 1000))


def _aware(value: dt.datetime) -> dt.datetime:
    """Attach UTC when a stored timestamp is naive (SQLite)."""
    if value.tzinfo is None:
        return value.replace(tzinfo=dt.UTC)
    return value
