"""Session filter options and aggregate stats.

Both endpoints read the workspace's sessions once and fold them into the
shapes the Sessions screen needs: the selectable filter dimensions (with counts
as hints) and the header counters. No N+1 — sessions load with their targets.
"""

from __future__ import annotations

import uuid
from collections import Counter

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.routers._session_data import load_sessions
from app.schemas import FilterOption, SessionFilterOptions, SessionStats
from slopolis_db.models import Repository, User

__all__ = ["build_filter_options", "build_stats"]

_STATUS_LABELS = {
    "queued": "Queued",
    "running": "Running",
    "done": "Done",
    "failed": "Failed",
    "cancelled": "Cancelled",
}


async def build_filter_options(
    db: AsyncSession, workspace_id: uuid.UUID
) -> SessionFilterOptions:
    """Return every filter dimension derived from existing sessions."""
    sessions = await load_sessions(db, workspace_id=workspace_id)
    repo_counts: Counter[str] = Counter()
    user_counts: Counter[uuid.UUID] = Counter()
    status_counts: Counter[str] = Counter()
    model_counts: Counter[str] = Counter()

    all_targets = [target for session in sessions for target in session.targets]
    repo_names = await _repo_names(db, {t.repository_id for t in all_targets})
    user_ids = {session.triggered_by_user_id for session in sessions}
    users = await _users(db, user_ids)

    for session in sessions:
        status_counts[session.status] += 1
        model_counts[session.model] += 1
        user_counts[session.triggered_by_user_id] += 1
        for target in session.targets:
            full_name = repo_names.get(target.repository_id)
            if full_name:
                repo_counts[full_name] += 1

    providers = await _model_providers(db, workspace_id, set(model_counts))
    return SessionFilterOptions(
        repositories=[
            FilterOption(value=name, label=name, hint=str(count))
            for name, count in sorted(repo_counts.items())
        ],
        users=[
            FilterOption(
                value=users[user_id].handle,
                label=users[user_id].name,
                hint=str(count),
            )
            for user_id, count in sorted(
                user_counts.items(), key=lambda item: users[item[0]].handle
            )
            if user_id in users
        ],
        statuses=[
            FilterOption(
                value=status,
                label=_STATUS_LABELS[status],
                hint=str(count),
            )
            for status, count in sorted(status_counts.items())
        ],
        models=[
            FilterOption(
                value=model_id,
                label=model_id,
                hint=providers.get(model_id),
            )
            for model_id, _count in sorted(model_counts.items())
        ],
    )


async def build_stats(db: AsyncSession, workspace_id: uuid.UUID) -> SessionStats:
    """Return the Sessions-screen header counters for the workspace."""
    sessions = await load_sessions(db, workspace_id=workspace_id)
    tokens = 0
    cost = 0.0
    for session in sessions:
        for target in session.targets:
            tokens += target.tokens
            cost += float(target.cost_usd)
    return SessionStats(
        total_sessions=len(sessions),
        running=sum(1 for session in sessions if session.status == "running"),
        failed=sum(1 for session in sessions if session.status == "failed"),
        tokens=tokens,
        cost_usd=round(cost, 6),
    )


async def _repo_names(
    db: AsyncSession, repo_ids: set[uuid.UUID]
) -> dict[uuid.UUID, str]:
    """Return repository full names keyed by id."""
    if not repo_ids:
        return {}
    rows = list(
        (
            await db.scalars(
                select(Repository).where(Repository.id.in_(repo_ids))
            )
        ).all()
    )
    return {row.id: row.full_name for row in rows}


async def _users(db: AsyncSession, user_ids: set[uuid.UUID]) -> dict[uuid.UUID, User]:
    """Return users keyed by id."""
    if not user_ids:
        return {}
    rows = list((await db.scalars(select(User).where(User.id.in_(user_ids)))).all())
    return {row.id: row for row in rows}


async def _model_providers(
    db: AsyncSession, workspace_id: uuid.UUID, model_ids: set[str]
) -> dict[str, str]:
    """Return provider labels keyed by model id, when the catalog knows them."""
    from slopolis_db.models import ModelCatalog

    if not model_ids:
        return {}
    rows = list(
        (
            await db.scalars(
                select(ModelCatalog).where(
                    ModelCatalog.workspace_id == workspace_id,
                    ModelCatalog.model_id.in_(model_ids),
                )
            )
        ).all()
    )
    return {row.model_id: row.provider for row in rows}
