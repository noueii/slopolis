"""Harness run-tree reads: the session's node hierarchy and its event log.

Both routes are the session-detail surface (spec 10.8): they resolve the session
workspace-scoped first, so a session outside the caller's workspace — and any run
id that is not part of the session asked for — is a 404 rather than an empty
page. The shapes are read-shaped for one screen: the whole tree is a single
query assembled in memory, and the event log is paginated by ``seq`` so a client
can replay a run without ever holding its whole history (spec v2 §7).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import (
    CurrentUserDep,
    DbSessionDep,
    RepoAccessCheckerDep,
    WorkspaceIdDep,
)
from app.errors import ApiError
from app.routers._session_data import load_session, require_session_access
from app.schemas import (
    AgentEventItem,
    AgentEventPage,
    AgentRunNode,
    AgentRunTreeResponse,
)
from app.services.repo_access import RepoAccessChecker
from slopolis_db.models import AgentEventRow, AgentRun, User

__all__ = ["event_item", "require_visible_session", "router"]

router = APIRouter(prefix="/sessions", tags=["sessions"])

#: Default page of an event replay: one screen for the node-detail pane.
_DEFAULT_LIMIT = 200

#: Hard cap on a replay page, so one request can never pull a whole run's log.
_MAX_LIMIT = 1000


@router.get("/{session_id}/runs/tree")
async def session_run_tree(
    session_id: uuid.UUID,
    db: DbSessionDep,
    workspace_id: WorkspaceIdDep,
    viewer: CurrentUserDep,
    checker: RepoAccessCheckerDep,
) -> AgentRunTreeResponse:
    """Return the session's runs, nested by ``parent_run_id``."""
    await require_visible_session(
        db, session_id, workspace_id, viewer, checker
    )
    rows = (
        await db.scalars(
            # One query for the whole tree; the order here is the order every
            # sibling list inherits, so the assembly below adds no sorting.
            select(AgentRun)
            .where(AgentRun.session_id == session_id)
            .order_by(AgentRun.started_at.asc().nulls_last(), AgentRun.id.asc())
        )
    ).all()
    return AgentRunTreeResponse(runs=_assemble_tree(rows))


@router.get("/{session_id}/runs/{run_id}/events")
async def run_events(
    session_id: uuid.UUID,
    run_id: uuid.UUID,
    db: DbSessionDep,
    workspace_id: WorkspaceIdDep,
    viewer: CurrentUserDep,
    checker: RepoAccessCheckerDep,
    afterSeq: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=_MAX_LIMIT)] = _DEFAULT_LIMIT,
) -> AgentEventPage:
    """Return the run's events after ``afterSeq``, oldest first."""
    await require_visible_session(
        db, session_id, workspace_id, viewer, checker
    )
    await _require_run(db, session_id, run_id)
    rows = (
        await db.execute(
            # ``seq > afterSeq`` is the cursor: a client that passes the previous
            # page's ``nextSeq`` back never re-reads a row it already has.
            select(AgentEventRow, AgentRun.parent_run_id)
            .join(AgentRun, AgentRun.id == AgentEventRow.run_id)
            .where(AgentEventRow.run_id == run_id, AgentEventRow.seq > afterSeq)
            .order_by(AgentEventRow.seq.asc())
            .limit(limit)
        )
    ).all()
    items = [event_item(event, parent_run_id) for event, parent_run_id in rows]
    # ``None`` rather than a repeated cursor when the page came back empty: the
    # client stops asking instead of re-requesting the same tail (spec v2 §7).
    return AgentEventPage(items=items, next_seq=items[-1].seq if items else None)


def event_item(event: AgentEventRow, parent_run_id: uuid.UUID | None) -> AgentEventItem:
    """Project an event row plus its run's parent onto the wire item.

    Shared with the SSE stream, which emits exactly this shape as its ``agent``
    event; ``parent_run_id`` lives on the run, so both callers join for it.
    """
    return AgentEventItem(
        id=event.id,
        run_id=event.run_id,
        parent_run_id=parent_run_id,
        seq=event.seq,
        type=event.type,
        payload=event.payload,
        created_at=event.created_at,
    )


def _assemble_tree(rows: Sequence[AgentRun]) -> list[AgentRunNode]:
    """Nest ordered run rows by parent, returning the roots.

    A row whose parent is missing — the parent was deleted, or its session was
    pruned mid-flight — is a root, so an orphan stays visible instead of being
    dropped from the tree it is still the only record of.
    """
    nodes = {row.id: _run_node(row) for row in rows}
    roots: list[AgentRunNode] = []
    for row in rows:
        parent = nodes.get(row.parent_run_id) if row.parent_run_id is not None else None
        if parent is None:
            roots.append(nodes[row.id])
        else:
            parent.children.append(nodes[row.id])
    return roots


def _run_node(row: AgentRun) -> AgentRunNode:
    """Project one run row onto a tree node with no children yet."""
    return AgentRunNode(
        id=row.id,
        session_id=row.session_id,
        target_id=row.target_id,
        parent_run_id=row.parent_run_id,
        level=row.level,
        role=row.role,
        model_id=row.model_id,
        objective=row.objective,
        status=row.status,
        tokens=row.tokens,
        cost_usd=float(row.cost_usd),
        started_at=row.started_at,
        ended_at=row.ended_at,
        error=row.error,
    )


async def require_visible_session(
    db: AsyncSession,
    session_id: uuid.UUID,
    workspace_id: uuid.UUID,
    viewer: User,
    checker: RepoAccessChecker,
) -> None:
    """Raise the session-detail refusals unless ``viewer`` may read the session.

    Workspace scoping is not enough on its own: the session-detail surface also
    answers with the viewer's own repository access (spec 10.8 §Access), through
    the same helper ``GET /sessions/{id}`` uses, so the tree, the event log, the
    SSE stream and the session itself cannot disagree about who may look.
    """
    session = await load_session(db, session_id, workspace_id=workspace_id)
    if session is None:
        raise ApiError(
            404,
            "session_not_found",
            "That review session does not exist.",
            detail=f"No session with id {session_id}.",
        )
    await require_session_access(db, session, viewer=viewer, checker=checker)


async def _require_run(
    db: AsyncSession, session_id: uuid.UUID, run_id: uuid.UUID
) -> None:
    """Raise a 404 unless the run is one of that session's runs."""
    exists = await db.scalar(
        select(AgentRun.id).where(
            AgentRun.id == run_id, AgentRun.session_id == session_id
        )
    )
    if exists is None:
        raise ApiError(
            404,
            "run_not_found",
            "That agent run does not exist.",
            detail=f"No run with id {run_id} in session {session_id}.",
        )
