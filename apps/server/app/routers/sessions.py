"""Session lifecycle endpoints: list, detail, create, patch, cancel, retry.

Listing honors the full :class:`SessionListParams` filter surface (q, repo,
user, status, range, page, pageSize, sort). Creation runs pre-flight first and
only persists a session when at least one target is valid — it never creates a
session on failure. Each persisted target is enqueued as one ARQ job. A manual
retry puts a finished session's failed or cancelled targets back on that same
queue, without re-running pre-flight (spec 10.5 §Manual retry) — and in the mode
each target's own last attempt calls for, so a target whose review already exists
is re-published rather than reviewed again (§Retrying a run that only failed to
publish).
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import (
    AppSettingsDep,
    ArqPoolDep,
    CurrentUserDep,
    DbSessionDep,
    PreflightServiceDep,
    RepoAccessCheckerDep,
    WorkspaceIdDep,
)
from app.errors import ApiError
from app.retry_actions import (
    PUBLISH,
    RETRYABLE_TARGET_STATUSES,
    attempt_retry_action,
    latest_attempts,
)
from app.routers._session_data import (
    accessible_views,
    load_session,
    load_sessions,
    require_session_access,
    serialize_targets,
)
from app.routers._session_query import filter_sessions, sort_sessions
from app.routers.session_create import create_session, enqueue_targets
from app.schemas import (
    CreatedSession,
    CreateReviewRequest,
    Paginated,
    RetryRequest,
    SessionFilterOptions,
    SessionListParams,
    SessionStats,
    SessionUpdateRequest,
)
from app.schemas import ReviewSession as ReviewSessionSchema
from app.serializers import serialize_session
from slopolis_core.domain import SessionStatus, TargetStatus
from slopolis_db.models import (
    Repository,
    ReviewSession,
    SessionTarget,
    SessionTargetRun,
    User,
)

__all__ = ["router"]

router = APIRouter(prefix="/sessions", tags=["sessions"])

#: Target statuses the queue already owns. Retrying one would duplicate the job
#: it is already running, so it is refused instead — except for a ``queued``
#: target whose job the queue lost, which :func:`_queue_lost` recognizes.
_ACTIVE_TARGET_STATUSES = (TargetStatus.QUEUED.value, TargetStatus.RUNNING.value)

#: How long a target may sit ``queued`` before a retry reads its job as lost.
#: A queued target is normally picked up and marked ``running`` within seconds, so
#: one still queued well past that, with no running attempt behind it, is a job
#: that died before it could touch the target. The case this exists for is a job
#: the queue refuses outright: a worker running older code rejects the arguments
#: the newer server enqueued with (``TypeError``) before the target is ever
#: loaded, leaving it ``queued`` with no job and no attempt — a dead end that only
#: a direct database edit used to get out of.
#:
#: Generous rather than tight, because re-enqueuing a target the queue does still
#: hold is what the guard must avoid: behind a saturated worker a target can wait
#: this long with its job still queued, and a retry would then duplicate it. The
#: window is the stand-in for knowing what the queue holds, and the alternative was
#: a session nothing could finish. It lives here, not beside the worker's knobs,
#: because the decision is the server's: it is the side that can put the job back.
QUEUE_STALE_AFTER_S = 120


@router.get("")
async def list_sessions(
    db: DbSessionDep,
    workspace_id: WorkspaceIdDep,
    viewer: CurrentUserDep,
    checker: RepoAccessCheckerDep,
    q: Annotated[str | None, Query()] = None,
    repo: Annotated[str | None, Query()] = None,
    user: Annotated[str | None, Query()] = None,
    status: Annotated[SessionStatus | None, Query()] = None,
    range: Annotated[str | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    pageSize: Annotated[int, Query(ge=1, le=100)] = 25,
    sort: Annotated[str, Query()] = "created_desc",
) -> Paginated:
    """Return a filtered, paginated, sorted page of sessions."""
    params = SessionListParams(
        q=q,
        repo=repo,
        user=user,
        status=status,
        range=range,
        page=page,
        page_size=pageSize,
        sort=sort,
    )
    sessions = await load_sessions(db, workspace_id=workspace_id)

    # The per-viewer access filter runs before the user's own filters: the page,
    # its totals, and its counters must all describe the readable set (a page
    # cannot claim 57 sessions while showing the three this viewer may read), and
    # no filter may match content the viewer cannot see (spec 10.8 §Access).
    views = await accessible_views(db, sessions, viewer=viewer, checker=checker)
    readable = {session.id: views[session.id].targets for session in sessions}
    rows = filter_sessions(
        [session for session in sessions if readable[session.id]],
        params,
        targets=readable,
        repo_ids=await _repo_ids(db, workspace_id, params.repo),
        user_ids=await _user_ids(db, workspace_id, params.user),
    )
    rows = sort_sessions(rows, params.sort)

    total = len(rows)
    total_pages = 0 if total == 0 else (total + params.page_size - 1) // params.page_size
    start = (params.page - 1) * params.page_size
    page_sessions = rows[start : start + params.page_size]

    items: list[ReviewSessionSchema] = []
    for session in page_sessions:
        triggered_by = await _triggered_by(db, session)
        targets = await serialize_targets(db, readable[session.id])
        items.append(serialize_session(session, triggered_by=triggered_by, targets=targets))

    return Paginated(
        items=items,
        page=params.page,
        page_size=params.page_size,
        total=total,
        total_pages=total_pages,
    )


@router.post("", status_code=201)
async def post_session(
    body: CreateReviewRequest,
    db: DbSessionDep,
    workspace_id: WorkspaceIdDep,
    user: CurrentUserDep,
    service: PreflightServiceDep,
    pool: ArqPoolDep,
) -> CreatedSession:
    """Run pre-flight and create a session with one target per valid PR."""
    return await create_session(
        db,
        workspace_id=workspace_id,
        user=user,
        body=body,
        service=service,
        pool=pool,
    )


@router.get("/filters")
async def session_filters(
    db: DbSessionDep, workspace_id: WorkspaceIdDep
) -> SessionFilterOptions:
    """Return every filter dimension for the Sessions screen."""
    from app.routers.session_filters import build_filter_options

    return await build_filter_options(db, workspace_id)


@router.get("/stats")
async def session_stats(
    db: DbSessionDep, workspace_id: WorkspaceIdDep
) -> SessionStats:
    """Return aggregate counters for the Sessions screen."""
    from app.routers.session_filters import build_stats

    return await build_stats(db, workspace_id)


@router.get("/{session_id}")
async def get_session(
    session_id: uuid.UUID,
    db: DbSessionDep,
    workspace_id: WorkspaceIdDep,
    viewer: CurrentUserDep,
    checker: RepoAccessCheckerDep,
    settings: AppSettingsDep,
) -> ReviewSessionSchema:
    """Return one session with only the targets and findings the viewer may read.

    Nothing left to show is 404 when the checks ran, and 403
    ``repo_access_unverified`` when they could not run at all (spec 10.8 §Access).
    """
    session = await _require_session(db, session_id, workspace_id)
    view = await require_session_access(db, session, viewer=viewer, checker=checker)
    triggered_by = await _triggered_by(db, session)
    # The author login comes from configuration, never a GitHub lookup: reading a
    # session must not reach GitHub (spec 10.7 §What the app shows), so a
    # deployment that has not set ``GITHUB_APP_SLUG`` shows the finding without
    # naming its author.
    slug = settings.core.github_app_slug
    app_login = f"{slug}[bot]" if slug else None
    targets = await serialize_targets(
        db, view.targets, with_findings=True, app_login=app_login
    )
    return serialize_session(session, triggered_by=triggered_by, targets=targets)


@router.patch("/{session_id}")
async def patch_session(
    session_id: uuid.UUID,
    body: SessionUpdateRequest,
    db: DbSessionDep,
    workspace_id: WorkspaceIdDep,
) -> ReviewSessionSchema:
    """Update a session's title or name."""
    session = await _require_session(db, session_id, workspace_id)
    title = (body.title or "").strip()
    name = (body.name or "").strip()
    if not title and not name:
        raise ApiError(422, "title_required", "Provide a non-empty title or name.")
    if title:
        session.title = title
    if name:
        session.name = name
    await db.commit()
    await db.refresh(session)
    return await _serialize(db, session)


@router.post("/{session_id}/cancel")
async def cancel_session(
    session_id: uuid.UUID, db: DbSessionDep, workspace_id: WorkspaceIdDep
) -> ReviewSessionSchema:
    """Cancel a session and every still-pending target."""
    session = await _require_session(db, session_id, workspace_id)
    if session.status in ("done", "failed", "cancelled"):
        raise ApiError(
            409, "session_not_cancellable", "This session has already finished."
        )
    session.status = SessionStatus.CANCELLED
    session.finished_at = dt.datetime.now(dt.UTC)
    for target in session.targets:
        if target.status in ("queued", "running"):
            target.status = "cancelled"
    await db.commit()
    await db.refresh(session)
    return await _serialize(db, session)


@router.post("/{session_id}/retry")
async def retry_session(
    session_id: uuid.UUID,
    db: DbSessionDep,
    workspace_id: WorkspaceIdDep,
    viewer: CurrentUserDep,
    checker: RepoAccessCheckerDep,
    pool: ArqPoolDep,
    body: RetryRequest | None = None,
) -> ReviewSessionSchema:
    """Put a finished session's retryable targets back on the queue.

    Manual retry is not a second submission: pre-flight already validated the
    links, coverage, and access, so nothing is re-validated here (spec 10.5
    §Manual retry). Earlier attempt rows are history and stay untouched — the
    worker opens the next attempt itself. Each target is re-queued in the mode its
    last attempt calls for, so a target that only failed to publish is not
    reviewed — and paid for — a second time.

    A queued target is normally the queue's to run, and is refused. The exception
    is one whose job the queue lost (:data:`QUEUE_STALE_AFTER_S`): nothing but a
    re-enqueue can run it, so it is recovered here rather than refused — the way
    out of a session the queue moved on from without finishing.
    """
    session = await _require_session(db, session_id, workspace_id)
    view = await require_session_access(db, session, viewer=viewer, checker=checker)
    # The lock goes before the decision below but after the access check, which can
    # call GitHub: two concurrent retries of one session would otherwise both read
    # these targets as failed, both flip them to queued, and both enqueue — the same
    # review bought twice.
    await _lock_session(db, session_id, workspace_id)
    # Statuses are re-read under that lock: `view.targets` is a pre-lock snapshot,
    # and the point of the lock is that the second caller sees what a first caller
    # in flight has already written.
    all_targets = await _reload_targets(db, session_id)
    # A queued target is the one status whose meaning has to be read rather than
    # known: whether the queue holds its job is what the running attempts say.
    running_attempts = await _running_attempts(
        db,
        [
            target.id
            for target in all_targets
            if target.status == TargetStatus.QUEUED.value
        ],
    )
    # The candidates are the targets this viewer may read, and therefore the ones
    # the detail screen could have offered them.
    readable = {target.id for target in view.targets}
    targets = _retry_selection(
        [target for target in all_targets if target.id in readable],
        body.target_ids if body else None,
        running_attempts=running_attempts,
        now=dt.datetime.now(dt.UTC),
    )
    # Each target comes back in the mode its own last attempt calls for (spec 10.5
    # §Retrying a run that only failed to publish): one whose review is already on
    # hand is re-published, everything else is reviewed again. Read here, because
    # the rule asks whether the target is retryable *now* — which the flip below
    # is what ends. The mode travels with the job, so the worker never re-derives
    # it, and never re-reviews a pull request the user has already paid for.
    # Only the attempt is asked, not the status: the selection above already
    # decided each of these targets may be retried — including the queued ones the
    # status rule alone would refuse.
    attempts = await latest_attempts(db, [target.id for target in targets])
    actions = {
        target.id: attempt_retry_action(attempts.get(target.id)) for target in targets
    }
    publish_retry = [target for target in targets if actions[target.id] == PUBLISH]
    review_retry = [target for target in targets if actions[target.id] != PUBLISH]
    recovered = [
        target for target in targets if target.status == TargetStatus.QUEUED.value
    ]
    for target in targets:
        target.status = SessionStatus.QUEUED.value
    # A recovered target was already queued, so that write changes nothing and the
    # row would keep the timestamp the decision above was made from: a second retry
    # would then read the same target as lost again and put a second job of it on
    # the queue. Being queued again is what the timestamp means, so the recovery
    # stamps it.
    requeued_at = dt.datetime.now(dt.UTC)
    for target in recovered:
        target.updated_at = requeued_at
    # The session only goes back to queued when nothing in it is still running: a
    # session with a live target is a review in progress, and reporting it as queued
    # (with no finish time) would move live work backwards. Left running, it is the
    # worker's recompute that finishes it once every target is terminal.
    if any(target.status == SessionStatus.RUNNING.value for target in all_targets):
        session.status = SessionStatus.RUNNING
    else:
        session.status = SessionStatus.QUEUED
    session.finished_at = None
    await db.commit()
    await db.refresh(session)
    # Enqueue only after the commit, so a worker can never pick up a job whose
    # session still reads as finished.
    if review_retry:
        await enqueue_targets(pool, session.id, review_retry)
    if publish_retry:
        await enqueue_targets(pool, session.id, publish_retry, mode=PUBLISH)
    return await _serialize(db, session)


async def _serialize(db: AsyncSession, session: ReviewSession) -> ReviewSessionSchema:
    """Serialize a session with its triggering user and target aggregates."""
    triggered_by = await _triggered_by(db, session)
    targets = await serialize_targets(db, session.targets)
    return serialize_session(session, triggered_by=triggered_by, targets=targets)


def _retry_selection(
    targets: list[SessionTarget],
    requested: list[uuid.UUID] | None,
    *,
    running_attempts: set[uuid.UUID],
    now: dt.datetime,
) -> list[SessionTarget]:
    """Return the targets a retry re-queues, or raise the refusal for it.

    Refusals come before the caller writes anything: a target the queue already
    owns is named rather than duplicated, and a selection with nothing retryable
    in it is a conflict, not a silent no-op (spec 10.5 §Manual retry). What counts
    as retryable is the statuses the retry rule reads, plus a queued target whose
    job is gone (:func:`_queue_lost`) — a target the queue owns only in name, and
    which nothing else can put back on it.
    """
    lost = {
        target.id
        for target in targets
        if _queue_lost(target, running_attempts=running_attempts, now=now)
    }

    def retryable(target: SessionTarget) -> bool:
        return target.status in RETRYABLE_TARGET_STATUSES or target.id in lost

    if requested:
        wanted = list(dict.fromkeys(requested))
        by_id = {target.id: target for target in targets}
        for target_id in wanted:
            if target_id not in by_id:
                raise ApiError(
                    404,
                    "target_not_found",
                    "That review target is not part of this session.",
                    detail=f"No target with id {target_id}.",
                )
        selection = [by_id[target_id] for target_id in wanted]
    else:
        selection = [target for target in targets if retryable(target)]

    busy = [
        target
        for target in selection
        if target.status in _ACTIVE_TARGET_STATUSES and not retryable(target)
    ]
    if busy:
        raise ApiError(
            409,
            "target_running",
            "A review target in this selection is already queued or running.",
            detail=f"Target {busy[0].id} is {busy[0].status}.",
        )

    requeued = [target for target in selection if retryable(target)]
    if not requeued:
        raise ApiError(
            409,
            "nothing_to_retry",
            "This session has no failed or cancelled targets to retry.",
        )
    return requeued


def _queue_lost(
    target: SessionTarget, *, running_attempts: set[uuid.UUID], now: dt.datetime
) -> bool:
    """Whether a queued target's job is gone rather than still on its way.

    Two things say the queue does not have it: no attempt row of the target is
    running (a worker that took the job marks one before it touches anything
    else), and the target has been queued longer than
    :data:`QUEUE_STALE_AFTER_S` — long enough that a job which was going to start
    it would have. A target with no attempt at all is exactly the case this is
    for: the job died before it could open one.
    """
    if target.status != TargetStatus.QUEUED.value:
        return False
    if target.id in running_attempts:
        return False
    updated_at = target.updated_at
    if updated_at.tzinfo is None:
        # SQLite hands timestamps back naive; they are stored as UTC.
        updated_at = updated_at.replace(tzinfo=dt.UTC)
    return updated_at <= now - dt.timedelta(seconds=QUEUE_STALE_AFTER_S)


async def _running_attempts(
    db: AsyncSession, target_ids: list[uuid.UUID]
) -> set[uuid.UUID]:
    """Return the targets with an attempt row still marked running.

    The evidence a queued target is being worked on: a worker opens its attempt
    (and marks the target running) before the attempt does anything, so a running
    row means the queue did not lose the job, whatever the target's own status
    says — including a retry that queued it while the previous attempt was still
    winding down.
    """
    if not target_ids:
        return set()
    rows = await db.scalars(
        select(SessionTargetRun.target_id).where(
            SessionTargetRun.target_id.in_(target_ids),
            SessionTargetRun.status == TargetStatus.RUNNING.value,
        )
    )
    return set(rows)


async def _require_session(
    db: AsyncSession, session_id: uuid.UUID, workspace_id: uuid.UUID
) -> ReviewSession:
    """Load a session or raise the standard 404."""
    session = await load_session(db, session_id, workspace_id=workspace_id)
    if session is None:
        raise _session_not_found(session_id)
    return session


def _session_not_found(session_id: uuid.UUID) -> ApiError:
    """The refusal for a session that is absent, or not the caller's to act on."""
    return ApiError(
        404,
        "session_not_found",
        "That review session does not exist.",
        detail=f"No session with id {session_id}.",
    )


async def _lock_session(
    db: AsyncSession, session_id: uuid.UUID, workspace_id: uuid.UUID
) -> None:
    """Lock one session row: the lock a retry's read-decide-write needs.

    Two retries racing on one session both read its targets as failed, both flip
    them to queued, and both enqueue, unless the second waits for the first to
    commit. The lock is the *session* row rather than the target rows a retry is
    about to flip, for two reasons: the decision also depends on every other target
    of the session (whether any is still running), which no lock on the selection
    would cover; and taking target rows while holding the session row would invert
    the worker's own order — it updates a target and then recomputes the session
    row — into a deadlock. SQLite (the tests) has no row locks; this compiles to a
    plain select there.
    """
    locked = await db.scalar(
        select(ReviewSession.id)
        .where(
            ReviewSession.id == session_id,
            ReviewSession.workspace_id == workspace_id,
        )
        .with_for_update()
    )
    if locked is None:
        raise _session_not_found(session_id)


async def _reload_targets(
    db: AsyncSession, session_id: uuid.UUID
) -> list[SessionTarget]:
    """Re-read a session's targets in display order, in place of loaded copies.

    Called under the retry's session lock, so the statuses it returns are the ones
    the last committed writer left behind rather than the pre-lock snapshot the
    access check worked from. ``populate_existing`` is what makes the copies already
    in the identity map take those fresh values.
    """
    rows = await db.scalars(
        select(SessionTarget)
        .where(SessionTarget.session_id == session_id)
        .order_by(SessionTarget.created_at, SessionTarget.number)
        .execution_options(populate_existing=True)
    )
    return list(rows.all())


async def _triggered_by(db: AsyncSession, session: ReviewSession) -> User:
    """Load the user who triggered the session."""
    user = await db.get(User, session.triggered_by_user_id)
    if user is None:
        raise ApiError(500, "session_user_missing", "The session's user no longer exists.")
    return user


async def _repo_ids(
    db: AsyncSession, workspace_id: uuid.UUID, full_name: str | None
) -> set[uuid.UUID]:
    """Return repository ids matching a full name (empty set means no filter)."""
    if not full_name:
        return set()
    rows = (
        await db.scalars(
            select(Repository.id).where(
                Repository.workspace_id == workspace_id,
                Repository.full_name == full_name,
            )
        )
    ).all()
    return set(rows)


async def _user_ids(
    db: AsyncSession, workspace_id: uuid.UUID, handle: str | None
) -> set[uuid.UUID]:
    """Return user ids matching a handle (empty set means no filter)."""
    if not handle:
        return set()
    rows = (
        await db.scalars(
            select(User.id).where(
                User.workspace_id == workspace_id,
                User.handle == handle,
            )
        )
    ).all()
    return set(rows)
