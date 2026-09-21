"""ORM → wire mapping helpers shared by the routers.

Keeping these out of the routers holds each router under the size ceiling and
gives the mapping one home: every :class:`~slopolis_db.models.review.ReviewSession`
becomes the same :class:`~app.schemas.ReviewSession` everywhere.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from app.retry_actions import RetryAction
from app.schemas import (
    CreatedSession,
    DashboardSession,
    RepositoryRef,
    UserRef,
    WorkspaceRef,
)
from app.schemas import (
    ReviewSession as ReviewSessionSchema,
)
from app.schemas import (
    SessionTarget as SessionTargetSchema,
)
from slopolis_core.domain import SessionStatus, TargetStatus
from slopolis_db.models import Repository, ReviewSession, SessionTarget, User, Workspace

__all__ = [
    "build_name",
    "repo_ref",
    "serialize_created_session",
    "serialize_dashboard_session",
    "serialize_session",
    "serialize_target",
    "user_ref",
    "workspace_ref",
]


def workspace_ref(workspace: Workspace | None) -> WorkspaceRef | None:
    """Map a workspace row onto the wire reference (``None`` stays ``None``)."""
    if workspace is None:
        return None
    return WorkspaceRef(id=str(workspace.id), name=workspace.name, slug=workspace.slug)


def user_ref(user: User) -> UserRef:
    """Map a user row onto the wire reference."""
    return UserRef(
        id=str(user.id),
        handle=user.handle,
        name=user.name,
        avatar_url=user.avatar_url,
        is_admin=user.is_admin,
    )


def repo_ref(repository: Repository) -> RepositoryRef:
    """Map a repository row onto the wire reference."""
    return RepositoryRef(
        id=str(repository.id),
        full_name=repository.full_name,
        private=repository.private,
        default_branch=repository.default_branch,
    )


def serialize_target(
    target: SessionTarget,
    findings_count: int,
    repository: Repository,
    *,
    retry_action: RetryAction | None,
) -> SessionTargetSchema:
    """Map one target with its per-target aggregates."""
    return SessionTargetSchema(
        id=str(target.id),
        repository=repo_ref(repository),
        number=target.number,
        title=target.title,
        url=target.url,
        head_branch=target.head_branch,
        status=target_status(target.status),
        retry_action=retry_action,
        findings_count=findings_count,
        tokens=target.tokens,
        cost_usd=_to_float(target.cost_usd),
        duration_ms=target.duration_ms,
    )


def serialize_session(
    session: ReviewSession,
    *,
    triggered_by: User,
    targets: list[SessionTargetSchema],
) -> ReviewSessionSchema:
    """Map a session with its targets and convenience aggregates."""
    total_tokens = sum(target.tokens for target in targets)
    total_cost = sum(target.cost_usd for target in targets)
    total_findings = sum(target.findings_count for target in targets)
    return ReviewSessionSchema(
        id=str(session.id),
        title=session.title,
        name=session.name,
        status=session_status(session.status),
        model=session.model,
        provider=session.provider,
        triggered_by=user_ref(triggered_by),
        created_at=session.created_at,
        started_at=session.started_at,
        finished_at=session.finished_at,
        duration_ms=_duration_ms(session.started_at, session.finished_at),
        targets=targets,
        target_count=len(targets),
        tokens=total_tokens,
        cost_usd=round(total_cost, 6),
        findings_count=total_findings,
        prompt=session.prompt,
    )


def serialize_dashboard_session(
    session: ReviewSession,
    *,
    targets: list[SessionTargetSchema],
) -> DashboardSession:
    """Map a session onto the compact dashboard history entry."""
    return DashboardSession(
        id=str(session.id),
        title=session.title,
        name=session.name,
        status=session_status(session.status),
        model=session.model,
        provider=session.provider,
        prompt=session.prompt,
        created_at=session.created_at,
        finished_at=session.finished_at,
        targets=targets,
        target_count=len(targets),
        findings_count=sum(target.findings_count for target in targets),
        cost_usd=round(sum(target.cost_usd for target in targets), 6),
    )


def serialize_created_session(
    session: ReviewSession, *, target_count: int
) -> CreatedSession:
    """Map a freshly created session onto the creation response."""
    return CreatedSession(
        id=str(session.id),
        title=session.title,
        name=session.name,
        status=session_status(session.status),
        model=session.model,
        provider=session.provider,
        created_at=session.created_at,
        target_count=target_count,
        prompt=session.prompt,
    )


def build_name(
    targets: list[tuple[str, int]], now: dt.datetime
) -> str:
    """Build the auto-name ``owner/name#123 +N more — Mon D``."""
    if not targets:
        return f"New review — {_month_day(now)}"
    full_name, number = targets[0]
    lead = f"{full_name}#{number}"
    extra = f" +{len(targets) - 1} more" if len(targets) > 1 else ""
    return f"{lead}{extra} — {_month_day(now)}"


def _month_day(value: dt.datetime) -> str:
    """Format a datetime as ``Mon D`` without platform-specific strftime."""
    months = (
        "Jan", "Feb", "Mar", "Apr", "May", "Jun",
        "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
    )
    return f"{months[value.month - 1]} {value.day}"


def _to_float(value: Decimal | float | int | None) -> float:
    """Coerce a numeric column to a JSON-safe float."""
    if value is None:
        return 0.0
    return float(value)


def session_status(value: str) -> SessionStatus:
    """Parse a stored session status into the wire enum."""
    return SessionStatus(value)


def target_status(value: str) -> TargetStatus:
    """Parse a stored target status into the wire enum."""
    return TargetStatus(value)


def _duration_ms(
    started_at: dt.datetime | None, finished_at: dt.datetime | None
) -> int | None:
    """Return wall-clock milliseconds between two timestamps, or ``None``."""
    if started_at is None or finished_at is None:
        return None
    return int((finished_at - started_at).total_seconds() * 1000)
