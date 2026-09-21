"""SSE session event stream: the session projection plus the run-tree events."""

from __future__ import annotations

import datetime as dt
import json
import uuid
from collections.abc import AsyncIterator
from typing import Any, cast

from app.deps import get_repo_access_checker
from app.routers.events import session_events
from sqlalchemy import select

from slopolis_db.models import AgentEventRow, AgentRun, ReviewSession, SessionTarget, User

from .conftest import ApiHarness


def _frames(text: str) -> list[tuple[str, str]]:
    """Parse a complete SSE body into its ``(event, data)`` frames."""
    frames: list[tuple[str, str]] = []
    name, data = "message", ""
    for line in text.splitlines():
        if line.startswith("event:"):
            name = line.removeprefix("event:").strip()
        elif line.startswith("data:"):
            data = line.removeprefix("data:").strip()
        elif not line and data:
            frames.append((name, data))
            name, data = "message", ""
    return frames


async def _seed_run_chain(
    session_factory: Any, session_id: uuid.UUID
) -> tuple[uuid.UUID, uuid.UUID]:
    """Persist a main -> pr -> sub chain; return the PR and sub run ids."""
    async with session_factory() as session:
        main = AgentRun(
            session_id=session_id,
            level="main",
            role="orchestrator.main",
            objective="Coordinate the review",
            status="running",
            started_at=dt.datetime.now(dt.UTC),
        )
        session.add(main)
        await session.flush()
        pr = AgentRun(
            session_id=session_id,
            parent_run_id=main.id,
            level="pr",
            role="orchestrator.pr",
            objective="Review PR #7",
            status="running",
            started_at=dt.datetime.now(dt.UTC),
        )
        session.add(pr)
        await session.flush()
        sub = AgentRun(
            session_id=session_id,
            parent_run_id=pr.id,
            level="sub",
            role="reviewer",
            objective="Review the change",
            status="running",
            started_at=dt.datetime.now(dt.UTC),
        )
        session.add(sub)
        await session.commit()
        return pr.id, sub.id


def _event(run_id: uuid.UUID, seq: int, kind: str) -> AgentEventRow:
    """Build one persisted harness event."""
    return AgentEventRow(
        run_id=run_id, seq=seq, type=kind, payload={"seq": seq, "role": "reviewer"}
    )


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


async def test_events_delivers_run_events_before_done(
    seeded: Any, session_factory: Any, build_harness: Any
) -> None:
    # Given a finished session whose sub-agent run wrote two events
    pr_id, sub_id = await _seed_run_chain(session_factory, seeded.session_id)
    async with session_factory() as session:
        session.add_all(
            [_event(sub_id, 1, "agent.started"), _event(sub_id, 2, "agent.finding")]
        )
        review = await session.get(ReviewSession, seeded.session_id)
        assert review is not None
        review.status = "done"
        await session.commit()
    harness: ApiHarness = await build_harness(user_id=seeded.user_id)

    # When the event stream is requested
    response = await harness.client.get(f"/api/sessions/{seeded.session_id}/events")

    # Then the events are drained ahead of the terminal frame, in seq order
    assert response.status_code == 200
    frames = _frames(response.text)
    assert [name for name, _ in frames] == ["agent", "agent", "session", "done"]

    # ...each carrying the event and the run it belongs to
    first = json.loads(frames[0][1])
    assert first == {
        "id": first["id"],
        "runId": str(sub_id),
        "parentRunId": str(pr_id),
        "seq": 1,
        "type": "agent.started",
        "payload": {"seq": 1, "role": "reviewer"},
        "createdAt": first["createdAt"],
    }
    assert [json.loads(data)["seq"] for _, data in frames[:2]] == [1, 2]


async def test_events_delivers_a_run_event_persisted_mid_stream(
    seeded: Any, session_factory: Any
) -> None:
    # Given a live session with a main run and the endpoint's own dependencies —
    # httpx buffers a response body, so the stream is pulled frame by frame here
    # to keep it open across the writes that follow
    async with session_factory() as session:
        viewer = await session.get(User, seeded.user_id)
        assert viewer is not None
        run = AgentRun(
            session_id=seeded.session_id,
            level="main",
            role="orchestrator.main",
            objective="Coordinate the review",
            status="running",
            started_at=dt.datetime.now(dt.UTC),
        )
        session.add(run)
        await session.commit()
        run_id = run.id

        response = await session_events(
            session_id=seeded.session_id,
            db=session,
            workspace_id=seeded.workspace_id,
            viewer=viewer,
            checker=await get_repo_access_checker(),
        )
        stream = cast(AsyncIterator[dict[str, str]], response.body_iterator)

        # ...which opens with the session snapshot only
        assert (await anext(stream))["event"] == "session"

        # When an event is persisted while the stream is open
        session.add(_event(run_id, 1, "agent.started"))
        await session.commit()

        # Then the next tick delivers it as an agent event tagged with its run
        frame = await anext(stream)
        assert frame["event"] == "agent"
        event = json.loads(frame["data"])
        assert event["runId"] == str(run_id)
        assert event["parentRunId"] is None
        assert event["seq"] == 1
        assert event["type"] == "agent.started"
        assert event["payload"] == {"seq": 1, "role": "reviewer"}
        assert event["createdAt"]

        # ...and when the session reaches a terminal status, the stream emits the
        # snapshot and closes, without replaying the event it already sent
        review = await session.get(ReviewSession, seeded.session_id)
        assert review is not None
        review.status = "done"
        await session.commit()

        assert (await anext(stream))["event"] == "session"
        assert (await anext(stream))["event"] == "done"
