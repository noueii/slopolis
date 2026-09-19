"""SSE session event stream."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select

from slopolis_db.models import ReviewSession, SessionTarget

from .conftest import ApiHarness


async def test_events_unknown_session_is_404(
    seeded: Any, build_harness: Any
) -> None:
    # Given a workspace without the requested session
    harness: ApiHarness = await build_harness(user_id=seeded.user_id)

    # When its event stream is requested
    response = await harness.client.get(f"/api/sessions/{uuid.uuid4()}/events")

    # Then the standard 404 envelope is returned before any stream opens
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "session_not_found"


async def test_events_terminal_session_streams_then_closes(
    seeded: Any, session_factory: Any, build_harness: Any
) -> None:
    # Given a finished session whose target is done
    async with session_factory() as session:
        review = await session.get(ReviewSession, seeded.session_id)
        assert review is not None
        review.status = "done"
        targets = (
            await session.scalars(
                select(SessionTarget).where(
                    SessionTarget.session_id == seeded.session_id
                )
            )
        ).all()
        for target in targets:
            target.status = "done"
        await session.commit()
    harness: ApiHarness = await build_harness(user_id=seeded.user_id)

    # When the event stream is requested
    response = await harness.client.get(f"/api/sessions/{seeded.session_id}/events")

    # Then it emits a session snapshot and a terminal done event, then closes
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: session" in response.text
    assert "event: done" in response.text
    assert '"status": "done"' in response.text
