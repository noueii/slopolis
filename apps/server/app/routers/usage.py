"""Usage attribution endpoint.

Rolls the workspace's usage records into totals, per-model and per-repository
breakdowns, and a daily time series. Aggregation happens in Python over the
workspace's records so the same code runs on Postgres and the in-memory SQLite
used by tests; record volume per workspace is small in v1.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections import defaultdict

from fastapi import APIRouter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import DbSessionDep, WorkspaceIdDep
from app.schemas import UsageBreakdown, UsagePoint, UsageResponse
from slopolis_db.models import Repository, SessionTarget, UsageRecord

__all__ = ["router"]

router = APIRouter(tags=["usage"])


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
