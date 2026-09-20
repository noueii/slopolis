"""Usage attribution endpoint.

Rolls the workspace's usage records into totals, per-model, per-repository, and
per-user breakdowns, and a daily time series. Aggregation happens in Python over
the workspace's records so the same code runs on Postgres and the in-memory
SQLite used by tests; record volume per workspace is small in v1.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections import defaultdict
from typing import NamedTuple

from fastapi import APIRouter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import DbSessionDep, WorkspaceIdDep
from app.schemas import UsageBreakdown, UsagePoint, UsageResponse
from slopolis_db.models import (
    Repository,
    ReviewSession,
    SessionTarget,
    UsageRecord,
    User,
)

__all__ = ["router"]

router = APIRouter(tags=["usage"])

# Records whose session cannot be resolved to a user are grouped here instead of
# being dropped, so the breakdowns always add up to the workspace totals.
UNATTRIBUTED_KEY = "unattributed"
UNATTRIBUTED_LABEL = "Unattributed"


class _Attribution(NamedTuple):
    """The session a usage record belongs to and the user who triggered it."""

    session_id: uuid.UUID | None
    user_id: uuid.UUID | None


@router.get("/usage")
async def get_usage(
    db: DbSessionDep, workspace_id: WorkspaceIdDep
) -> UsageResponse:
    """Return totals, breakdowns, and a daily series for the workspace."""
    records = list(
        (
            await db.scalars(
                select(UsageRecord).where(UsageRecord.workspace_id == workspace_id)
            )
        ).all()
    )
    repo_names = await _repo_names(db, records)

    total_tokens = sum(record.total_tokens for record in records)
    total_cost = sum(float(record.cost_usd) for record in records)
    sessions = {record.session_id for record in records if record.session_id is not None}

    return UsageResponse(
        total_tokens=total_tokens,
        total_cost_usd=round(total_cost, 6),
        total_sessions=len(sessions),
        by_model=_by_model(records),
        by_repository=_by_repository(records, repo_names),
        by_user=await _by_user(db, records),
        series=_series(records),
    )


def _by_model(records: list[UsageRecord]) -> list[UsageBreakdown]:
    """Aggregate usage per model id."""
    tokens: dict[str, int] = defaultdict(int)
    cost: dict[str, float] = defaultdict(float)
    sessions: dict[str, set[uuid.UUID | None]] = defaultdict(set)
    label: dict[str, str] = {}
    for record in records:
        tokens[record.model_id] += record.total_tokens
        cost[record.model_id] += float(record.cost_usd)
        sessions[record.model_id].add(record.session_id)
        label[record.model_id] = record.provider
    return [
        UsageBreakdown(
            key=model_id,
            label=label[model_id],
            tokens=tokens[model_id],
            cost_usd=round(cost[model_id], 6),
            sessions=len(sessions[model_id]),
        )
        for model_id in sorted(tokens, key=lambda key: tokens[key], reverse=True)
    ]


def _by_repository(
    records: list[UsageRecord], repo_names: dict[uuid.UUID, str]
) -> list[UsageBreakdown]:
    """Aggregate usage per repository reached through the records' targets."""
    tokens: dict[str, int] = defaultdict(int)
    cost: dict[str, float] = defaultdict(float)
    sessions: dict[str, set[uuid.UUID | None]] = defaultdict(set)
    for record in records:
        name = repo_names.get(record.target_id or uuid.UUID(int=0))
        if name is None:
            continue
        tokens[name] += record.total_tokens
        cost[name] += float(record.cost_usd)
        sessions[name].add(record.session_id)
    return [
        UsageBreakdown(
            key=name,
            label=name,
            tokens=tokens[name],
            cost_usd=round(cost[name], 6),
            sessions=len(sessions[name]),
        )
        for name in sorted(tokens, key=lambda key: tokens[key], reverse=True)
    ]


async def _by_user(db: AsyncSession, records: list[UsageRecord]) -> list[UsageBreakdown]:
    """Aggregate usage per user who triggered the records' sessions.

    A usage record carries no user of its own, so it is attributed through its
    session; records that resolve to no user are grouped under ``unattributed``
    rather than dropped.
    """
    attributions = await _attributions(db, records)
    handles = await _handles(
        db, {row.user_id for row in attributions if row.user_id is not None}
    )
    tokens: dict[str, int] = defaultdict(int)
    cost: dict[str, float] = defaultdict(float)
    sessions: dict[str, set[uuid.UUID]] = defaultdict(set)
    label: dict[str, str] = {}
    for record, attribution in zip(records, attributions, strict=True):
        user_id = attribution.user_id
        handle = None if user_id is None else handles.get(user_id)
        if user_id is None or handle is None:
            key, name = UNATTRIBUTED_KEY, UNATTRIBUTED_LABEL
        else:
            key, name = str(user_id), handle
        tokens[key] += record.total_tokens
        cost[key] += float(record.cost_usd)
        if attribution.session_id is not None:
            sessions[key].add(attribution.session_id)
        label[key] = name
    return [
        UsageBreakdown(
            key=key,
            label=label[key],
            tokens=tokens[key],
            cost_usd=round(cost[key], 6),
            sessions=len(sessions[key]),
        )
        for key in sorted(tokens, key=lambda key: tokens[key], reverse=True)
    ]


async def _attributions(
    db: AsyncSession, records: list[UsageRecord]
) -> list[_Attribution]:
    """Resolve each record to its session and that session's triggering user.

    A record names its session directly, or only its target (the worker records
    one usage event per target), so a target-only record is followed to its
    session. A record whose session no longer resolves keeps both ids ``None``,
    which is what groups it under ``unattributed``.
    """
    session_ids = {
        record.session_id for record in records if record.session_id is not None
    }
    target_ids = {
        record.target_id for record in records if record.target_id is not None
    }
    target_sessions: dict[uuid.UUID, uuid.UUID] = {}
    if target_ids:
        targets = (
            await db.scalars(
                select(SessionTarget).where(SessionTarget.id.in_(target_ids))
            )
        ).all()
        target_sessions = {target.id: target.session_id for target in targets}
    users: dict[uuid.UUID, uuid.UUID] = {}
    reachable = session_ids | set(target_sessions.values())
    if reachable:
        for session in await db.scalars(
            select(ReviewSession).where(ReviewSession.id.in_(reachable))
        ):
            users[session.id] = session.triggered_by_user_id
    resolved: list[_Attribution] = []
    for record in records:
        session_id = record.session_id
        if session_id is None and record.target_id is not None:
            session_id = target_sessions.get(record.target_id)
        resolved.append(
            _Attribution(
                session_id=session_id,
                user_id=None if session_id is None else users.get(session_id),
            )
        )
    return resolved


async def _handles(db: AsyncSession, user_ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
    """Map user ids onto their GitHub handles."""
    if not user_ids:
        return {}
    users = (await db.scalars(select(User).where(User.id.in_(user_ids)))).all()
    return {user.id: user.handle for user in users}


def _series(records: list[UsageRecord]) -> list[UsagePoint]:
    """Bucket usage into a daily series, oldest first."""
    tokens: dict[dt.date, int] = defaultdict(int)
    cost: dict[dt.date, float] = defaultdict(float)
    sessions: dict[dt.date, set[uuid.UUID | None]] = defaultdict(set)
    for record in records:
        day = _as_date(record.created_at)
        tokens[day] += record.total_tokens
        cost[day] += float(record.cost_usd)
        sessions[day].add(record.session_id)
    return [
        UsagePoint(
            date=day,
            tokens=tokens[day],
            cost_usd=round(cost[day], 6),
            sessions=len(sessions[day]),
        )
        for day in sorted(tokens)
    ]


async def _repo_names(
    db: AsyncSession, records: list[UsageRecord]
) -> dict[uuid.UUID, str]:
    """Map target ids referenced by usage records onto repository full names."""
    target_ids = {record.target_id for record in records if record.target_id is not None}
    if not target_ids:
        return {}
    targets = list(
        (
            await db.scalars(
                select(SessionTarget).where(SessionTarget.id.in_(target_ids))
            )
        ).all()
    )
    repo_ids = {target.repository_id for target in targets}
    if not repo_ids:
        return {}
    repos = list(
        (await db.scalars(select(Repository).where(Repository.id.in_(repo_ids)))).all()
    )
    names = {repo.id: repo.full_name for repo in repos}
    return {
        target.id: names[target.repository_id]
        for target in targets
        if target.repository_id in names
    }


def _as_date(value: dt.datetime) -> dt.date:
    """Return the calendar date of a timestamp."""
    if value.tzinfo is None:
        return value.date()
    return value.astimezone(dt.UTC).date()
