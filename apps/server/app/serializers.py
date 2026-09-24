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
    RepositoryRef,
    UserRef,
    WorkspaceRef,
)
from app.schemas import (
    Finding as FindingSchema,
)
from app.schemas import (
    ReviewSession as ReviewSessionSchema,
)
from app.schemas import (
    SessionTarget as SessionTargetSchema,
)
from slopolis_core.domain import SessionStatus, Severity, TargetStatus
from slopolis_db.models import (
    Finding,
    Repository,
    ReviewSession,
    SessionTarget,
    User,
    Workspace,
)

__all__ = [
    "build_name",
    "repo_ref",
    "review_comment_url",
    "serialize_created_session",
    "serialize_finding",
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


def review_comment_url(
    repo_full_name: str, number: int, comment_id: int | None
) -> str | None:
    """Build the URL of a review comment posted on a pull request.

    GitHub anchors a review comment by its id on the pull request page, which is
    what the comment's own ``html_url`` carries; only the id is stored, so the
    link is built here.
    """
    if comment_id is None:
        return None
    return f"https://github.com/{repo_full_name}/pull/{number}#discussion_r{comment_id}"


def serialize_finding(
    finding: Finding,
    *,
    repository: Repository,
    number: int,
    app_login: str | None = None,
) -> FindingSchema:
    """Map one finding row onto the wire, with the comment it was posted as.

    One rule decides every comment field: the comment id is the finding's
    ``github_comment_id`` only when the publisher recorded it as ``posted``, and
    ``comment_url``, ``author``, ``posted_at`` and ``diff_hunk`` all follow that
    single value — so they can never disagree about whether a comment exists.

    ``repository`` and ``number`` name the pull request the comment lives on:
    a finding row keeps only the comment id, not the URL it resolves to.

    ``app_login`` is the login the comment is posted as, taken from
    configuration rather than looked up, so rendering a finding never reaches
    GitHub. ``posted_at`` is the row's ``updated_at``: the moment the publisher
    stamped ``posted`` and the comment id, which is why a publish retry dates
    the comment rather than the review the finding came from.
    """
    comment_id = finding.github_comment_id if finding.posted else None
    has_comment = comment_id is not None
    return FindingSchema(
        path=finding.path,
        line=finding.line,
        severity=Severity(finding.severity),
        category=finding.category,
        message=finding.message,
        suggestion=finding.suggestion,
        comment_url=review_comment_url(repository.full_name, number, comment_id),
        author=app_login if has_comment else None,
        posted_at=finding.updated_at if has_comment else None,
        diff_hunk=finding.diff_hunk if has_comment else None,
    )


def serialize_target(
    target: SessionTarget,
    findings_count: int,
    repository: Repository,
    *,
    retry_action: RetryAction | None,
    findings: list[FindingSchema] | None = None,
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
        findings=findings,
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
