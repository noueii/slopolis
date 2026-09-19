"""Session creation: pre-flight gate, targets, and ARQ enqueue."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select

from slopolis_db.models import ReviewSession, SessionTarget

from .conftest import ApiHarness, FakeGateway, make_ref

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
