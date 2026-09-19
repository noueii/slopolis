"""Shared session-persistence helpers (queries, aggregates, live projection).

Sessions are loaded with their targets, repositories, findings, and triggering
user in one round trip. This module owns the eager-loading strategy and the
per-target aggregate computation so the sessions and dashboard routers stay
focused on shaping responses.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.schemas import SessionTarget as SessionTargetSchema
from app.serializers import serialize_target
from slopolis_db.models import (
    Finding,
    Repository,
    ReviewSession,
    SessionTarget,
)

__all__ = [
    "finding_counts_for",
    "load_session",
    "load_sessions",
    "repositories_for_targets",
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
