"""Session filter options and aggregate stats."""

from __future__ import annotations

from typing import Any

from .conftest import ApiHarness


async def test_filter_options_reflect_sessions(
    seeded: Any, build_harness: Any
) -> None:
    # Given one seeded session in acme/api triggered by octocat
    harness: ApiHarness = await build_harness(user_id=seeded.user_id)

    # When the filter options are requested
    response = await harness.client.get("/api/sessions/filters")

    # Then every dimension is populated from the data
    assert response.status_code == 200
    body = response.json()
    assert [option["value"] for option in body["repositories"]] == ["acme/api"]
    assert [option["value"] for option in body["users"]] == ["octocat"]
    assert [option["value"] for option in body["statuses"]] == ["queued"]
    assert [option["value"] for option in body["models"]] == ["claude-sonnet-4"]
    assert body["users"][0]["label"] == "Mona Lisa"


async def test_stats_totals(seeded: Any, build_harness: Any) -> None:
    # Given one queued session with 120 tokens
    harness: ApiHarness = await build_harness(user_id=seeded.user_id)

    # When stats are requested
    response = await harness.client.get("/api/sessions/stats")

    # Then the totals reflect the seeded session
    assert response.status_code == 200
    body = response.json()
    assert body["totalSessions"] == 1
    assert body["running"] == 0
    assert body["failed"] == 0
    assert body["tokens"] == 120
    assert body["costUsd"] == 0.01
