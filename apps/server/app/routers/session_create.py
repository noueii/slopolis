"""Session creation: pre-flight gate, persistence, and per-target enqueue.

Creation runs pre-flight first and refuses to persist anything unless at least
one target resolves — a failed pre-flight never leaves a half-built session
behind. Each persisted target is enqueued as exactly one ARQ job named
``review_target`` with ``(session_id, target_id)``; :func:`enqueue_targets` is
that enqueue, shared with the manual-retry path so a retried target gets the
very job submission would have put on the queue (spec 10.5 §Manual retry).
"""

from __future__ import annotations

import datetime as dt
import re
import uuid
import zlib
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.workspace import WorkspaceConfigAdapter
from app.deps import ArqPool
from app.errors import ApiError
from app.routers.catalog import (
    DEFAULT_MODEL_ID,
    DEFAULT_PROVIDER,
)
from app.routers.reviews import explain_parked
from app.schemas import CreatedSession, CreateReviewRequest
from app.serializers import build_name, serialize_created_session
from slopolis_core.domain import SessionStatus
from slopolis_core.harness import MAIN_AGENT, AgentStatus, HarnessLevel
from slopolis_core.preflight.models import PreflightRequest as CorePreflightRequest
from slopolis_core.preflight.models import PrReference
from slopolis_core.preflight.service import PreflightService
from slopolis_db.models import (
    AgentRun,
    GitHubInstallation,
    Repository,
    ReviewSession,
    SessionTarget,
    User,
    Workspace,
)

__all__ = ["create_session", "enqueue_targets", "review_title_from_title"]

_SUBJECT_RE = re.compile(r"^[a-z]+(?:\([^)]*\))?:\s*(.+)$", re.IGNORECASE)
_JOB_NAME = "review_target"

#: Session statuses that still hold a slot; every other status is terminal.
_ACTIVE_STATUSES = (SessionStatus.QUEUED.value, SessionStatus.RUNNING.value)

#: How many live session names a cap refusal lists before summarising the rest.
_MAX_LISTED_SESSIONS = 5

#: The refusal text per cap code, so the caller knows which switch to ask about.
_CAP_MESSAGES = {
    "session_limit_reached": (
        "This submission would exceed the workspace's concurrent session limit."
    ),
    "user_daily_limit_reached": "You have reached your daily session limit.",
}


async def create_session(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    user: User,
    body: CreateReviewRequest,
    service: PreflightService,
    pool: ArqPool,
    now: dt.datetime | None = None,
) -> CreatedSession:
    """Validate, persist, and enqueue a session with one target per valid PR."""
    urls = [value.strip() for value in body.pr_urls if value.strip()]
    if not urls:
        raise ApiError(422, "no_targets", "Add at least one pull request.")

    created_at = now or dt.datetime.now(dt.UTC)
    # The caps come before pre-flight: a submission that cannot be accepted must
    # not spend GitHub or live-model calls, and must not leave a session row.
    await _enforce_caps(db, workspace_id=workspace_id, user=user, now=created_at)

    outcome = await service.run(
        CorePreflightRequest(pr_urls=urls), user_login=user.handle
    )
    # A parked repository is left out of the coverage set, so pre-flight can only
    # call it "not covered". Name the real reason before refusing the submission.
    outcome = await explain_parked(db, workspace_id, outcome)
    if not outcome.valid:
        raise ApiError(
            422,
            "no_valid_targets",
            "None of the pasted links belong to a covered repository.",
            detail="; ".join(outcome.notices) or None,
        )

    reference = outcome.valid[0]
    model_id, provider = await _resolve_model(db, workspace_id)
    session = ReviewSession(
        workspace_id=workspace_id,
        title=review_title_from_title(reference.title),
        name=build_name(
            [(item.repository.full_name, item.number) for item in outcome.valid],
            created_at,
        ),
        prompt=body.prompt.strip() if body.prompt else None,
        status="queued",
        model=model_id,
        provider=provider,
        triggered_by_user_id=user.id,
    )
    db.add(session)
    await db.flush()
    await _ensure_main_run(db, session, started_at=created_at)

    targets: list[SessionTarget] = []
    for item in outcome.valid:
        repository = await _resolve_repository(db, workspace_id, item)
        target = SessionTarget(
            session_id=session.id,
            repository_id=repository.id,
            number=item.number,
            title=item.title,
            url=item.url,
            head_branch=item.repository.default_branch or "main",
            status="queued",
        )
        db.add(target)
        targets.append(target)
    await db.flush()
    await db.commit()

    await enqueue_targets(pool, session.id, targets)

    return serialize_created_session(session, target_count=len(targets))


async def enqueue_targets(
    pool: ArqPool, session_id: uuid.UUID, targets: Sequence[SessionTarget]
) -> None:
    """Put one ``review_target`` job per target on the queue.

    The single place that names the job and fixes its argument order: a manual
    retry must enqueue literally the work submission does (spec 10.5 §Manual
    retry), so both paths call this instead of each spelling the job out.
    """
    for target in targets:
        await pool.enqueue_job(_JOB_NAME, str(session_id), str(target.id))


async def _enforce_caps(
    db: AsyncSession, *, workspace_id: uuid.UUID, user: User, now: dt.datetime
) -> None:
    """Refuse a submission that would exceed the workspace's caps (spec 10.10).

    Both counts look only at sessions **still holding a slot** — a cap bounds
    concurrency, not observed usage — and both exclude the submission being
    refused, which has not been written yet.

    Reading the caps is all a workspace that never set one pays for: when both
    are ``NULL`` this returns before counting anything, so "unset means
    unlimited" is literally a no-op rather than merely equivalent.
    """
    caps = (
        await db.execute(
            select(
                Workspace.max_concurrent_sessions,
                Workspace.max_sessions_per_user_per_day,
            ).where(Workspace.id == workspace_id)
        )
    ).one_or_none()
    if caps is None:
        return
    concurrent, daily = caps
    if concurrent is None and daily is None:
        return

    if concurrent is not None:
        active = await _active_session_names(db, workspace_id=workspace_id)
        if len(active) >= concurrent:
            raise _cap_error(
                "session_limit_reached", "maxConcurrentSessions", concurrent, active
            )

    if daily is not None:
        today = await _active_session_names(
            db,
            workspace_id=workspace_id,
            user_id=user.id,
            # "Since 00:00 UTC" of the day the submission is being made.
            since=dt.datetime.combine(now.date(), dt.time.min, tzinfo=dt.UTC),
        )
        if len(today) >= daily:
            raise _cap_error(
                "user_daily_limit_reached", "maxSessionsPerUserPerDay", daily, today
            )


async def _active_session_names(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID | None = None,
    since: dt.datetime | None = None,
) -> list[str]:
    """Names of the workspace's (or the caller's) sessions still holding a slot."""
    query = select(ReviewSession.name).where(
        ReviewSession.workspace_id == workspace_id,
        ReviewSession.status.in_(_ACTIVE_STATUSES),
    )
    if user_id is not None:
        query = query.where(ReviewSession.triggered_by_user_id == user_id)
    if since is not None:
        query = query.where(ReviewSession.created_at >= since)
    return list((await db.scalars(query.order_by(ReviewSession.created_at))).all())


def _cap_error(code: str, cap: str, limit: int, active: Sequence[str]) -> ApiError:
    """The refusal for one cap: which cap, how many are live, and which ones."""
    listed = ", ".join(active[:_MAX_LISTED_SESSIONS])
    if len(active) > _MAX_LISTED_SESSIONS:
        listed += f" and {len(active) - _MAX_LISTED_SESSIONS} more"
    sessions = "session" if len(active) == 1 else "sessions"
    return ApiError(
        409,
        code,
        _CAP_MESSAGES[code],
        detail=(
            f"{cap} is {limit}; {len(active)} {sessions} already "
            f"queued or running: {listed}."
        ),
    )


async def _ensure_main_run(
    db: AsyncSession, session: ReviewSession, *, started_at: dt.datetime
) -> None:
    """Create the session's ``main`` run so its tree exists from submit (spec §15).

    A session whose target jobs all fail to start still has a root node this way,
    and the worker's lazy creation finds this row instead of opening a second one.
    Find-or-create rather than blind insert because both paths may race for the
    same session.
    """
    existing = await db.scalar(
        select(AgentRun.id).where(
            AgentRun.session_id == session.id,
            AgentRun.level == HarnessLevel.MAIN.value,
        )
    )
    if existing is not None:
        return
    db.add(
        AgentRun(
            session_id=session.id,
            target_id=None,
            parent_run_id=None,
            level=HarnessLevel.MAIN.value,
            role=MAIN_AGENT,
            model_id=None,
            objective=f"Coordinate the review of {session.name}",
            status=AgentStatus.RUNNING.value,
            started_at=started_at,
        )
    )
    await db.flush()


async def _resolve_model(db: AsyncSession, workspace_id: uuid.UUID) -> tuple[str, str]:
    """Return the workspace default model, or the catalog fallback."""
    resolved = await WorkspaceConfigAdapter(db, workspace_id).default_model()
    if resolved is not None:
        return resolved
    return DEFAULT_MODEL_ID, DEFAULT_PROVIDER


async def _resolve_repository(
    db: AsyncSession, workspace_id: uuid.UUID, reference: PrReference
) -> Repository:
    """Find the repository row, creating it (and its installation) if needed."""
    full_name = reference.repository.full_name
    existing = await db.scalar(
        select(Repository).where(
            Repository.workspace_id == workspace_id,
            Repository.full_name == full_name,
        )
    )
    if existing is not None:
        return existing

    owner = full_name.split("/", 1)[0]
    installation = await db.scalar(
        select(GitHubInstallation).where(
            GitHubInstallation.workspace_id == workspace_id,
            GitHubInstallation.account_login == owner,
        )
    )
    if installation is None:
        installation = GitHubInstallation(
            workspace_id=workspace_id,
            installation_id=_synthetic_installation_id(owner),
            account_login=owner,
            account_type="Unknown",
        )
        db.add(installation)
        await db.flush()

    repository = Repository(
        workspace_id=workspace_id,
        installation_id=installation.id,
        github_id=0,
        full_name=full_name,
        private=reference.repository.private,
        default_branch=reference.repository.default_branch or "main",
        connected=True,
    )
    db.add(repository)
    await db.flush()
    return repository


def _synthetic_installation_id(owner: str) -> int:
    """Derive a stable, negative installation id for an un-synced account.

    Real installations carry GitHub's positive ids; the negative space is free
    for placeholders created before the first sync and keeps the uniqueness
    constraint satisfied deterministically per account.
    """
    checksum = zlib.crc32(owner.lower().encode("utf-8"))
    return -(checksum % 2_000_000_000 + 1)


def review_title_from_title(title: str) -> str:
    """Derive a short review title from a PR title (mirrors the mock helper)."""
    match = _SUBJECT_RE.match(title)
    subject = match.group(1) if match else title
    phrase = " ".join(subject.split()[:6]).rstrip(".,;:!?")
    if not phrase:
        return title
    return phrase[0].upper() + phrase[1:]
