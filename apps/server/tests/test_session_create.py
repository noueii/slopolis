"""Session creation: pre-flight gate, targets, and ARQ enqueue."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import event, func, select

from slopolis_db.models import Repository, ReviewSession, SessionTarget, User, Workspace

from .conftest import (
    ApiHarness,
    FakeGateway,
    FakeLiveCheck,
    make_ref,
    seed_session,
)

_URL_A = "https://github.com/acme/api/pull/11"
_URL_B = "https://github.com/acme/web/pull/22"
_UNKNOWN = "https://github.com/acme/api/pull/not-a-number"


async def test_create_session_persists_targets_and_enqueues(
    seeded: Any, session_factory: Any, build_harness: Any
) -> None:
    # Given a gateway that resolves two PRs
    gateway = FakeGateway(
        refs={
            _URL_A: make_ref("acme/api", 11),
            _URL_B: make_ref("acme/web", 22),
        }
    )
    harness: ApiHarness = await build_harness(
        user_id=seeded.user_id, gateway=gateway
    )

    # When a session is created with both links
    response = await harness.client.post(
        "/api/sessions",
        json={"prUrls": [_URL_A, _URL_B], "prompt": "Look for auth bugs"},
    )

    # Then the session is queued with one target per PR
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "queued"
    assert body["targetCount"] == 2
    assert body["model"] == "claude-sonnet-4"
    assert body["prompt"] == "Look for auth bugs"

    async with session_factory() as session:
        created = await session.get(ReviewSession, uuid.UUID(body["id"]))
        assert created is not None
        targets = await session.scalar(
            select(func.count(SessionTarget.id)).where(
                SessionTarget.session_id == created.id
            )
        )
    assert targets == 2

    # And exactly one ARQ job per target is enqueued
    assert len(harness.pool.jobs) == 2
    assert {job[0] for job in harness.pool.jobs} == {"review_target"}
    assert {job[1][0] for job in harness.pool.jobs} == {body["id"]}


async def test_create_session_rejects_empty_request(
    seeded: Any, build_harness: Any
) -> None:
    # Given a seeded workspace and no links
    harness: ApiHarness = await build_harness(user_id=seeded.user_id)

    # When an empty submission is created
    response = await harness.client.post("/api/sessions", json={"prUrls": []})

    # Then a 422 no_targets envelope is returned and nothing is enqueued
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "no_targets"
    assert harness.pool.jobs == []


async def test_create_session_rejects_unresolvable_links(
    seeded: Any, session_factory: Any, build_harness: Any
) -> None:
    # Given a gateway that resolves nothing
    harness: ApiHarness = await build_harness(
        user_id=seeded.user_id, gateway=FakeGateway()
    )

    # When a submission with an unresolvable link is created
    response = await harness.client.post("/api/sessions", json={"prUrls": [_UNKNOWN]})

    # Then a 422 no_valid_targets envelope is returned
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "no_valid_targets"
    assert harness.pool.jobs == []

    async with session_factory() as session:
        count = await session.scalar(select(func.count(ReviewSession.id)))
    assert count == 1


async def test_create_session_refuses_a_parked_repository_by_its_reason(
    seeded: Any, session_factory: Any, build_harness: Any
) -> None:
    # Given a repository the workspace has parked: its row says so, and parking
    # is what drops it from the coverage set pre-flight is handed
    async with session_factory() as session:
        row = await session.get(Repository, seeded.repository_id)
        assert row is not None
        row.enabled = False
        await session.commit()
    gateway = FakeGateway(refs={_URL_A: make_ref("acme/api", 11)}, covered=["acme/web"])
    harness: ApiHarness = await build_harness(
        user_id=seeded.user_id, gateway=gateway
    )

    # When a PR in it is submitted
    response = await harness.client.post("/api/sessions", json={"prUrls": [_URL_A]})

    # Then the refusal names the disabled state, not a missing installation, and
    # nothing is persisted or enqueued
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "no_valid_targets"
    assert "acme/api is disabled in slopolis" in error["detail"]
    assert "not covered" not in error["detail"]
    assert harness.pool.jobs == []

    async with session_factory() as session:
        count = await session.scalar(select(func.count(ReviewSession.id)))
    assert count == 1


async def test_create_session_keeps_the_uncovered_notice_for_an_unknown_repository(
    seeded: Any, build_harness: Any
) -> None:
    # Given a link to a repository the workspace holds no row for: nothing was
    # ever switched off about it
    other = "https://github.com/other/repo/pull/3"
    gateway = FakeGateway(refs={other: make_ref("other/repo", 3)}, covered=["acme/api"])
    harness: ApiHarness = await build_harness(
        user_id=seeded.user_id, gateway=gateway
    )

    # When it is submitted
    response = await harness.client.post("/api/sessions", json={"prUrls": [other]})

    # Then the refusal keeps core's coverage notice rather than a parked reason
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "no_valid_targets"
    assert "other/repo is not covered by the GitHub App installation." in error["detail"]
    assert "disabled" not in error["detail"]


# --- stopgap caps (spec 10.10) ----------------------------------------------


async def set_caps(
    session_factory: Any, workspace_id: uuid.UUID, **caps: int | None
) -> None:
    """Pin the workspace's caps for the test; a cap left unset stays unlimited."""
    async with session_factory() as session:
        workspace = await session.get(Workspace, workspace_id)
        assert workspace is not None
        for name, value in caps.items():
            setattr(workspace, name, value)
        await session.commit()


async def seed_live_session(
    session_factory: Any,
    seeded: Any,
    *,
    number: int,
    user_id: uuid.UUID | None = None,
    status: str = "queued",
    created_at: dt.datetime | None = None,
) -> None:
    """Add one session to the seeded workspace, optionally by another member."""
    async with session_factory() as session:
        workspace = await session.get(Workspace, seeded.workspace_id)
        user = await session.get(User, user_id or seeded.user_id)
        repository = await session.get(Repository, seeded.repository_id)
        assert workspace is not None and user is not None and repository is not None
        await seed_session(
            session,
            workspace=workspace,
            user=user,
            repository=repository,
            number=number,
            status=status,
            created_at=created_at,
        )


async def test_the_concurrent_cap_refuses_before_preflight_or_any_row(
    seeded: Any, session_factory: Any, build_harness: Any
) -> None:
    # Given a workspace capped at two concurrent sessions with two already live
    await set_caps(session_factory, seeded.workspace_id, max_concurrent_sessions=2)
    await seed_live_session(session_factory, seeded, number=8)
    gateway = FakeGateway(refs={_URL_A: make_ref("acme/api", 11)})
    live_check = FakeLiveCheck()
    harness: ApiHarness = await build_harness(
        user_id=seeded.user_id, gateway=gateway, live_check=live_check
    )

    # When another submission arrives
    response = await harness.client.post("/api/sessions", json={"prUrls": [_URL_A]})

    # Then it is refused with the cap's own code, naming the cap, the count, and
    # what is already running, so the user knows which switch to ask about
    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "session_limit_reached"
    assert "maxConcurrentSessions is 2" in error["detail"]
    assert "2 sessions already queued or running" in error["detail"]
    assert "acme/api#7 - Jan 1" in error["detail"]

    # ...without spending a GitHub or live-model call...
    assert gateway.access_calls == []
    assert live_check.calls == []

    # ...and without a session row or an enqueued job
    async with session_factory() as session:
        count = await session.scalar(select(func.count(ReviewSession.id)))
    assert count == 2
    assert harness.pool.jobs == []


async def test_no_cap_leaves_submissions_unlimited(
    seeded: Any, session_factory: Any, build_harness: Any
) -> None:
    # Given a workspace that never set a cap, with three sessions already live
    await seed_live_session(session_factory, seeded, number=8)
    await seed_live_session(session_factory, seeded, number=9)
    gateway = FakeGateway(refs={_URL_A: make_ref("acme/api", 11)})
    harness: ApiHarness = await build_harness(user_id=seeded.user_id, gateway=gateway)

    # When another submission arrives
    response = await harness.client.post("/api/sessions", json={"prUrls": [_URL_A]})

    # Then it is accepted: an unset cap means unlimited, not zero
    assert response.status_code == 201, response.text
    assert harness.pool.jobs and harness.pool.jobs[0][0] == "review_target"


async def test_an_unset_cap_costs_a_submission_no_session_count(
    seeded: Any, engine: Any, build_harness: Any
) -> None:
    # Given a workspace with no caps at all, and a recorder over its SQL
    statements: list[str] = []

    def record(
        _conn: Any,
        _cursor: Any,
        statement: str,
        _parameters: Any,
        _context: Any,
        _executemany: Any,
    ) -> None:
        statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", record)
    gateway = FakeGateway(refs={_URL_A: make_ref("acme/api", 11)})
    harness: ApiHarness = await build_harness(user_id=seeded.user_id, gateway=gateway)

    try:
        # When a submission is accepted
        response = await harness.client.post("/api/sessions", json={"prUrls": [_URL_A]})
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", record)

    # Then no session was counted: an unset cap is a literal no-op, not a
    # comparison against zero
    assert response.status_code == 201, response.text
    assert [statement for statement in statements if "FROM review_sessions" in statement] == []


async def test_the_concurrent_cap_ignores_terminal_sessions(
    seeded: Any, session_factory: Any, build_harness: Any
) -> None:
    # Given a workspace capped at one, whose only session has already finished
    await set_caps(session_factory, seeded.workspace_id, max_concurrent_sessions=1)
    async with session_factory() as session:
        row = await session.get(ReviewSession, seeded.session_id)
        assert row is not None
        row.status = "done"
        await session.commit()
    gateway = FakeGateway(refs={_URL_A: make_ref("acme/api", 11)})
    harness: ApiHarness = await build_harness(user_id=seeded.user_id, gateway=gateway)

    # When a submission arrives
    response = await harness.client.post("/api/sessions", json={"prUrls": [_URL_A]})

    # Then it is accepted: a finished session no longer holds a slot
    assert response.status_code == 201, response.text


async def test_the_daily_cap_counts_only_the_callers_live_sessions_today(
    seeded: Any, session_factory: Any, build_harness: Any
) -> None:
    # Given a per-user daily cap of one, where the caller already has one live
    # session today while another member's live session and the caller's own
    # finished one must not count against them
    await set_caps(session_factory, seeded.workspace_id, max_sessions_per_user_per_day=1)
    async with session_factory() as session:
        other = User(
            workspace_id=seeded.workspace_id,
            github_id=7007,
            handle="teammate",
            name="Team Mate",
            avatar_url=None,
        )
        session.add(other)
        await session.commit()
        other_id = other.id
    await seed_live_session(session_factory, seeded, number=8, user_id=other_id)
    await seed_live_session(
        session_factory,
        seeded,
        number=9,
        status="done",
        created_at=dt.datetime.now(dt.UTC),
    )
    gateway = FakeGateway(refs={_URL_A: make_ref("acme/api", 11)})
    harness: ApiHarness = await build_harness(user_id=seeded.user_id, gateway=gateway)

    # When the caller submits
    response = await harness.client.post("/api/sessions", json={"prUrls": [_URL_A]})

    # Then the refusal names their daily cap and only their own live session
    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "user_daily_limit_reached"
    assert "maxSessionsPerUserPerDay is 1; 1 session already queued or running" in (
        error["detail"]
    )
    assert "acme/api#7 - Jan 1" in error["detail"]
    assert harness.pool.jobs == []


async def test_the_daily_cap_ignores_other_users_and_earlier_days(
    seeded: Any, session_factory: Any, build_harness: Any
) -> None:
    # Given a per-user daily cap of one, another member's live session today,
    # and the caller's own session from one minute before 00:00 UTC today
    await set_caps(session_factory, seeded.workspace_id, max_sessions_per_user_per_day=1)
    midnights = dt.datetime.combine(
        dt.datetime.now(dt.UTC).date(), dt.time.min, tzinfo=dt.UTC
    )
    async with session_factory() as session:
        other = User(
            workspace_id=seeded.workspace_id,
            github_id=7008,
            handle="teammate",
            name="Team Mate",
            avatar_url=None,
        )
        session.add(other)
        await session.commit()
        other_id = other.id
    await seed_live_session(session_factory, seeded, number=8, user_id=other_id)
    async with session_factory() as session:
        row = await session.get(ReviewSession, seeded.session_id)
        assert row is not None
        row.created_at = midnights - dt.timedelta(minutes=1)
        await session.commit()
    gateway = FakeGateway(refs={_URL_A: make_ref("acme/api", 11)})
    harness: ApiHarness = await build_harness(user_id=seeded.user_id, gateway=gateway)

    # When the caller submits
    response = await harness.client.post("/api/sessions", json={"prUrls": [_URL_A]})

    # Then it is accepted: neither someone else's session nor yesterday's counts
    assert response.status_code == 201, response.text
