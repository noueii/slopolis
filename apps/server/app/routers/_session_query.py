"""Pure filter and sort helpers for the sessions list.

Kept separate from the router so the query semantics are unit-testable in
isolation and the router stays a thin HTTP shell. Every filter dimension of
:class:`SessionListParams` is applied here; ``repo`` and ``user`` are resolved
to id sets by the caller before filtering.
"""

from __future__ import annotations

import datetime as dt
import uuid

from app.schemas import SessionListParams
from slopolis_db.models import ReviewSession

__all__ = ["filter_sessions", "range_cutoff", "sort_sessions"]

_RANGE_DAYS = {"24h": 1, "7d": 7, "30d": 30, "90d": 90}


def filter_sessions(
    sessions: list[ReviewSession],
    params: SessionListParams,
    *,
    repo_ids: set[uuid.UUID],
    user_ids: set[uuid.UUID],
) -> list[ReviewSession]:
    """Return only the sessions satisfying every supplied filter."""
    cutoff = range_cutoff(params.range)
    return [
        session
        for session in sessions
        if _matches(session, params, repo_ids=repo_ids, user_ids=user_ids, cutoff=cutoff)
    ]


def _matches(
    session: ReviewSession,
    params: SessionListParams,
    *,
    repo_ids: set[uuid.UUID],
    user_ids: set[uuid.UUID],
    cutoff: dt.datetime | None,
) -> bool:
    """Return whether a session satisfies every active filter."""
    if params.q and not _matches_query(session, params.q):
        return False
    if params.repo and not any(t.repository_id in repo_ids for t in session.targets):
        return False
    if params.user and session.triggered_by_user_id not in user_ids:
        return False
    if params.status and session.status != params.status:
        return False
    return not (cutoff is not None and aware(session.created_at) < cutoff)


def range_cutoff(range_value: str | None) -> dt.datetime | None:
    """Return the inclusive lower bound for a range preset, or ``None``."""
    days = _RANGE_DAYS.get(range_value or "")
    if days is None:
        return None
    return dt.datetime.now(dt.UTC) - dt.timedelta(days=days)


def _matches_query(session: ReviewSession, query: str) -> bool:
    """Free-text match across session fields and every target's fields."""
    needle = query.lower()
    haystacks = [
        session.name,
        session.title,
        session.model,
        session.provider,
        session.status,
    ]
    for target in session.targets:
        haystacks.extend(
            [target.title, target.url, target.head_branch, f"#{target.number}"]
        )
    return any(needle in value.lower() for value in haystacks)


def sort_sessions(
    sessions: list[ReviewSession], sort: str
) -> list[ReviewSession]:
    """Return a new list sorted by the requested order (default newest first)."""
    if sort == "created_asc":
        return sorted(sessions, key=lambda s: aware(s.created_at))
    if sort == "cost_desc":
        return sorted(sessions, key=_cost, reverse=True)
    if sort == "tokens_desc":
        return sorted(sessions, key=_tokens, reverse=True)
    return sorted(sessions, key=lambda s: aware(s.created_at), reverse=True)


def _cost(session: ReviewSession) -> float:
    """Total cost across a session's targets."""
    return sum(float(target.cost_usd) for target in session.targets)


def _tokens(session: ReviewSession) -> int:
    """Total tokens across a session's targets."""
    return sum(target.tokens for target in session.targets)


def aware(value: dt.datetime) -> dt.datetime:
    """Attach UTC when SQLite returns a naive timestamp."""
    if value.tzinfo is None:
        return value.replace(tzinfo=dt.UTC)
    return value
