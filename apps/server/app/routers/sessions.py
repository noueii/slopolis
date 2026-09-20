"""Session lifecycle endpoints: list, detail, create, patch, cancel.

Listing honors the full :class:`SessionListParams` filter surface (q, repo,
user, status, range, page, pageSize, sort). Creation runs pre-flight first and
only persists a session when at least one target is valid — it never creates a
session on failure. Each persisted target is enqueued as one ARQ job.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import (
    ArqPoolDep,
    CurrentUserDep,
    DbSessionDep,
    PreflightServiceDep,
    RepoAccessCheckerDep,
    WorkspaceIdDep,
)
from app.errors import ApiError
from app.routers._session_data import (
    accessible_views,
    load_session,
    load_sessions,
    require_session_access,
    serialize_targets,
)
from app.routers._session_query import filter_sessions, sort_sessions
from app.routers.session_create import create_session
from app.schemas import (
    CreatedSession,
    CreateReviewRequest,
    Paginated,
    SessionFilterOptions,
    SessionListParams,
    SessionStats,
    SessionUpdateRequest,
)
from app.schemas import ReviewSession as ReviewSessionSchema
from app.serializers import serialize_session
from slopolis_core.domain import SessionStatus
from slopolis_db.models import Repository, ReviewSession, User

__all__ = ["router"]

router = APIRouter(prefix="/sessions", tags=["sessions"])


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
) -> ReviewSessionSchema:
    """Return one session with only the targets and findings the viewer may read.

    Nothing left to show is 404 when the checks ran, and 403
    ``repo_access_unverified`` when they could not run at all (spec 10.8 §Access).
    """
    session = await _require_session(db, session_id, workspace_id)
    view = await require_session_access(db, session, viewer=viewer, checker=checker)
    triggered_by = await _triggered_by(db, session)
    targets = await serialize_targets(db, view.targets)
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


async def _serialize(db: AsyncSession, session: ReviewSession) -> ReviewSessionSchema:
    """Serialize a session with its triggering user and target aggregates."""
    triggered_by = await _triggered_by(db, session)
    targets = await serialize_targets(db, session.targets)
    return serialize_session(session, triggered_by=triggered_by, targets=targets)


async def _require_session(
    db: AsyncSession, session_id: uuid.UUID, workspace_id: uuid.UUID
) -> ReviewSession:
    """Load a session or raise the standard 404."""
    session = await load_session(db, session_id, workspace_id=workspace_id)
    if session is None:
        raise ApiError(
            404,
            "session_not_found",
            "That review session does not exist.",
            detail=f"No session with id {session_id}.",
        )
    return session


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
