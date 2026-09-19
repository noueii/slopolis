"""Pre-flight validation endpoint."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select

from slopolis_db.models import ReviewSession

from .conftest import (
    ApiHarness,
    FakeGateway,
    FakeLiveCheck,
    FakeWorkspace,
    make_ref,
)

_VALID_URL = "https://github.com/acme/api/pull/11"
_UNKNOWN_URL = "https://github.com/other/repo/pull/3"


async def test_preflight_splits_valid_and_invalid(
    seeded: Any, build_harness: Any
) -> None:
    # Given a gateway that resolves one link and rejects the other
    gateway = FakeGateway(refs={_VALID_URL: make_ref("acme/api", 11)})
    harness: ApiHarness = await build_harness(
        user_id=seeded.user_id, gateway=gateway
    )

    # When pre-flight runs over both links
    response = await harness.client.post(
        "/api/reviews/preflight", json={"prUrls": [_VALID_URL, _UNKNOWN_URL]}
    )

    # Then the outcome splits them and records a notice
    assert response.status_code == 200
    body = response.json()
    assert [item["url"] for item in body["valid"]] == [_VALID_URL]
    assert body["valid"][0]["repository"]["fullName"] == "acme/api"
    assert body["invalid"] == [_UNKNOWN_URL]
    assert body["notices"]


async def test_preflight_reports_uncovered_repository(
    seeded: Any, build_harness: Any
) -> None:
    # Given a PR in a repo the installation does not cover
    gateway = FakeGateway(
        refs={_VALID_URL: make_ref("acme/private")}, covered=["acme/api"]
    )
    harness: ApiHarness = await build_harness(
        user_id=seeded.user_id, gateway=gateway
    )

    # When pre-flight runs
    response = await harness.client.post(
        "/api/reviews/preflight", json={"prUrls": [_VALID_URL]}
    )

    # Then the link is invalid with an explanatory notice
    body = response.json()
    assert body["valid"] == []
    assert body["invalid"] == [_VALID_URL]
    assert any("not covered" in notice for notice in body["notices"])


async def test_preflight_fails_without_model(
    seeded: Any, build_harness: Any
) -> None:
    # Given a workspace with no assigned model
    harness: ApiHarness = await build_harness(
        user_id=seeded.user_id, workspace=FakeWorkspace(model=None)
    )

    # When pre-flight runs
    response = await harness.client.post(
        "/api/reviews/preflight", json={"prUrls": [_VALID_URL]}
    )

    # Then no target validates and the missing model is surfaced
    body = response.json()
    assert body["valid"] == []
    assert any("No review model" in notice for notice in body["notices"])


async def test_preflight_fails_when_live_check_fails(
    seeded: Any, build_harness: Any
) -> None:
    # Given a gateway that resolves the link but a failing live model check
    gateway = FakeGateway(refs={_VALID_URL: make_ref("acme/api", 11)})
    harness: ApiHarness = await build_harness(
        user_id=seeded.user_id,
        gateway=gateway,
        live_check=FakeLiveCheck(fail=True),
    )

    # When pre-flight runs
    response = await harness.client.post(
        "/api/reviews/preflight", json={"prUrls": [_VALID_URL]}
    )

    # Then the link is rejected and no session is created
    body = response.json()
    assert body["valid"] == []
    assert any("Live model check failed" in notice for notice in body["notices"])


async def test_preflight_never_creates_a_session(
    seeded: Any, session_factory: Any, build_harness: Any
) -> None:
    # Given a successful pre-flight run
    gateway = FakeGateway(refs={_VALID_URL: make_ref("acme/api", 11)})
    harness: ApiHarness = await build_harness(
        user_id=seeded.user_id, gateway=gateway
    )
    await harness.client.post(
        "/api/reviews/preflight", json={"prUrls": [_VALID_URL]}
    )

    # Then the session table is unchanged
    async with session_factory() as session:
        count = await session.scalar(select(func.count(ReviewSession.id)))
    assert count == 1
