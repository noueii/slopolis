"""Server-Sent Events stream for one session's status changes.

The stream carries a lightweight projection of the session and its targets —
polled once per interval and emitted only on change — plus every persisted
harness event, drained from the run tree on the same tick so the node-detail
pane follows a review live (spec v2 §7). It ends when the session reaches a
terminal status, so a client that reconnects after completion gets one final
snapshot and a clean close.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncGenerator

from fastapi import APIRouter
from sqlalchemy import ColumnElement, case, select, true
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.deps import (
    CurrentUserDep,
    DbSessionDep,
    RepoAccessCheckerDep,
    WorkspaceIdDep,
)
from app.routers._session_data import load_session
from app.routers.runs import event_item, require_visible_session
from slopolis_db.models import AgentEventRow, AgentRun, SessionTarget

__all__ = ["router"]

router = APIRouter(prefix="/sessions", tags=["sessions"])

_TERMINAL = frozenset({"done", "failed", "cancelled"})
_POLL_SECONDS = 1.0


@router.get("/{session_id}/events")
async def session_events(
    session_id: uuid.UUID,
    db: DbSessionDep,
    workspace_id: WorkspaceIdDep,
    viewer: CurrentUserDep,
    checker: RepoAccessCheckerDep,
) -> EventSourceResponse:
    """Stream status changes for one session as SSE."""
    await require_visible_session(db, session_id, workspace_id, viewer, checker)

    async def event_stream() -> AsyncGenerator[dict[str, str], None]:
        last: str | None = None
        # Highest seq already sent, per run. Empty on connect, so the first tick
        # replays each run's whole log and a late client still sees every step;
        # after that the cursor only tracks the tail (spec §7).
        seen: dict[uuid.UUID, int] = {}
        while True:
            for frame in await _pending_events(db, session_id, seen):
                yield frame
            payload = await _snapshot(db, session_id, workspace_id)
            if payload is None:
                return
            fingerprint = json.dumps(payload, sort_keys=True)
            if fingerprint != last:
                last = fingerprint
                yield {"event": "session", "data": fingerprint}
            if payload["status"] in _TERMINAL:
                # Drain once more before closing: events persisted alongside the
                # terminal status would otherwise land after ``done`` and race
                # the client's teardown.
                for frame in await _pending_events(db, session_id, seen):
                    yield frame
                yield {"event": "done", "data": fingerprint}
                return
            await _end_tick(db)
            await asyncio.sleep(_POLL_SECONDS)

    return EventSourceResponse(event_stream())


async def _end_tick(db: AsyncSession) -> None:
    """Close the tick's read transaction before sleeping.

    The stream outlives every request-scoped transaction, so leaving the implicit
    transaction open would pin a database snapshot for as long as the client
    stays connected.
    """
    await db.rollback()


async def _pending_events(
    db: AsyncSession, session_id: uuid.UUID, seen: dict[uuid.UUID, int]
) -> list[dict[str, str]]:
    """Return the session's events past each run's cursor, as ``agent`` frames.

    One query per tick whatever the tree's shape: the per-run cursor is the
    in-memory watermark compiled into a CASE, so a tick costs a single round trip
    instead of one per run. Rows advance ``seen`` as they are read, so an event
    is emitted exactly once.
    """
    rows = (
        await db.execute(
            select(AgentEventRow, AgentRun.parent_run_id)
            .join(AgentRun, AgentRun.id == AgentEventRow.run_id)
            .where(AgentRun.session_id == session_id, _newer_than(seen))
            # Grouped by run so each node's events stay contiguous and ordered by
            # the seq the contract promises; runs themselves are independent.
            .order_by(AgentEventRow.run_id, AgentEventRow.seq)
        )
    ).all()
    frames: list[dict[str, str]] = []
    for event, parent_run_id in rows:
        seen[event.run_id] = event.seq
        item = event_item(event, parent_run_id)
        frames.append({"event": "agent", "data": item.model_dump_json(by_alias=True)})
    return frames


def _newer_than(seen: dict[uuid.UUID, int]) -> ColumnElement[bool]:
    """The per-run ``seq >`` predicate, as one expression per tick.

    Folded into a CASE over the in-memory watermarks so runs never cost a query
    each. A run with no watermark yet is simply not filtered: the connect tick
    replays its log from the first event.
    """
    if not seen:
        return true()
    return AgentEventRow.seq > case(seen, value=AgentEventRow.run_id, else_=0)


async def _snapshot(
    db: AsyncSession, session_id: uuid.UUID, workspace_id: uuid.UUID
) -> dict[str, object] | None:
    """Read a session's current status projection, or ``None`` if it vanished."""
    session = await load_session(db, session_id, workspace_id=workspace_id)
    if session is None:
        return None
    return {
        "id": str(session.id),
        "status": session.status,
        "targets": [_target_view(target) for target in session.targets],
    }


def _target_view(target: SessionTarget) -> dict[str, object]:
    """Project one target down to its id and status for the stream."""
    return {"id": str(target.id), "number": target.number, "status": target.status}
