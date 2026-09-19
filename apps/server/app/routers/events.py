"""Server-Sent Events stream for one session's status changes.

Phase 0 streams a lightweight projection of the session and its targets,
polling the database once per interval and emitting only on change. The stream
ends when the session reaches a terminal status, so a client that reconnects
after completion gets one final snapshot and a clean close. Phase 1 layers
``agent.*`` events onto the same channel.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncGenerator

from fastapi import APIRouter
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.deps import CurrentUserDep, DbSessionDep, WorkspaceIdDep
from app.errors import ApiError
from app.routers._session_data import load_session
from slopolis_db.models import SessionTarget

__all__ = ["router"]

router = APIRouter(prefix="/sessions", tags=["sessions"])

_TERMINAL = frozenset({"done", "failed", "cancelled"})
_POLL_SECONDS = 1.0


@router.get("/{session_id}/events")
async def session_events(
    session_id: uuid.UUID,
    db: DbSessionDep,
    workspace_id: WorkspaceIdDep,
    _user: CurrentUserDep,
) -> EventSourceResponse:
    """Stream status changes for one session as SSE."""
    existing = await load_session(db, session_id, workspace_id=workspace_id)
    if existing is None:
        raise ApiError(
            404,
            "session_not_found",
            "That review session does not exist.",
            detail=f"No session with id {session_id}.",
        )

    async def event_stream() -> AsyncGenerator[dict[str, str], None]:
        last: str | None = None
        while True:
            payload = await _snapshot(db, session_id, workspace_id)
            if payload is None:
                return
            fingerprint = json.dumps(payload, sort_keys=True)
            if fingerprint != last:
                last = fingerprint
                yield {"event": "session", "data": fingerprint}
            if payload["status"] in _TERMINAL:
                yield {"event": "done", "data": fingerprint}
                return
            await asyncio.sleep(_POLL_SECONDS)

    return EventSourceResponse(event_stream())


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
