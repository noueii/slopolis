"""Session listing: filters, sorting, and pagination."""

from __future__ import annotations

from typing import Any

from .conftest import ApiHarness


async def test_list_sessions_returns_aggregates(
    seeded: Any, build_harness: Any
) -> None:
    # Given one seeded queued session
    harness: ApiHarness = await build_harness(user_id=seeded.user_id)

    # When sessions are listed with defaults
    response = await harness.client.get("/api/sessions")

    # Then the page carries the session and its aggregates
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["page"] == 1
    assert body["pageSize"] == 25
    assert body["totalPages"] == 1
    item = body["items"][0]
    assert item["id"] == str(seeded.session_id)
    assert item["targetCount"] == 1
    assert item["tokens"] == 120
    assert item["costUsd"] == 0.01
    assert item["triggeredBy"]["handle"] == "octocat"
    assert item["targets"][0]["repository"]["fullName"] == "acme/api"


async def test_list_sessions_applies_filters(
    seeded: Any, build_harness: Any
) -> None:
    # Given one seeded session
    harness: ApiHarness = await build_harness(user_id=seeded.user_id)

    # When each filter dimension is exercised
    match = await harness.client.get("/api/sessions", params={"q": "token"})
    miss = await harness.client.get("/api/sessions", params={"q": "zzz-none"})
    repo = await harness.client.get("/api/sessions", params={"repo": "acme/api"})
    other_repo = await harness.client.get(
        "/api/sessions", params={"repo": "acme/other"}
    )
    user = await harness.client.get("/api/sessions", params={"user": "octocat"})
    ghost = await harness.client.get("/api/sessions", params={"user": "ghost"})
    status = await harness.client.get("/api/sessions", params={"status": "running"})

    # Then only the matching filters return rows
    assert match.json()["total"] == 1
    assert miss.json()["total"] == 0
    assert repo.json()["total"] == 1
    assert other_repo.json()["total"] == 0
    assert user.json()["total"] == 1
    assert ghost.json()["total"] == 0
    assert status.json()["total"] == 0


async def test_list_sessions_paginates(seeded: Any, build_harness: Any) -> None:
    # Given one session and a page size of one
    harness: ApiHarness = await build_harness(user_id=seeded.user_id)

    # When the second page is requested
    response = await harness.client.get(
        "/api/sessions", params={"page": 2, "pageSize": 1}
    )

    # Then it is empty while totals still report the full set
    body = response.json()
    assert body["items"] == []
    assert body["total"] == 1
    assert body["totalPages"] == 1
    assert body["page"] == 2


async def test_list_sessions_supports_range_and_sort(
    seeded: Any, build_harness: Any
) -> None:
    # Given a recent session
    harness: ApiHarness = await build_harness(user_id=seeded.user_id)

    # When the 24h range and a cost sort are applied
    recent = await harness.client.get("/api/sessions", params={"range": "24h"})
    sorted_page = await harness.client.get(
        "/api/sessions", params={"sort": "cost_desc"}
    )

    # Then the recent session is in range and sorting succeeds
    assert recent.json()["total"] == 1
    assert sorted_page.json()["items"][0]["id"] == str(seeded.session_id)
