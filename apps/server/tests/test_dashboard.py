"""Dashboard home aggregation."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from .conftest import ApiHarness, seed_session


async def test_dashboard_aggregates_sessions(
    seeded: Any, build_harness: Any
) -> None:
    # Given one live (queued) session in the workspace
    harness: ApiHarness = await build_harness(user_id=seeded.user_id)

    # When the dashboard is requested
    response = await harness.client.get("/api/dashboard")

    # Then the summary, running list, and history are populated
    assert response.status_code == 200
    body = response.json()
    assert body["scope"] == "All repositories"
    assert body["summary"]["totalSessions"] == 1
    assert body["summary"]["tokens"] == 120
    assert body["summary"]["spendUsd"] == 0.01
    assert len(body["running"]) == 1
    assert body["running"][0]["prLabel"] == "acme/api#7"
    assert body["running"][0]["progress"] >= 0
    assert len(body["recent"]) == 1
    assert body["recent"][0]["targetCount"] == 1


async def test_dashboard_scopes_to_repository(
    seeded: Any, build_harness: Any
) -> None:
    # Given a session scoped to acme/api
    harness: ApiHarness = await build_harness(user_id=seeded.user_id)

    # When the dashboard is filtered to the repo, then to an unknown one
    scoped = await harness.client.get("/api/dashboard", params={"repo": "acme/api"})
    unknown = await harness.client.get("/api/dashboard", params={"repo": "acme/none"})

    # Then only the matching scope reports sessions
    assert scoped.json()["scope"] == "acme/api"
    assert scoped.json()["summary"]["totalSessions"] == 1
    assert unknown.json()["summary"]["totalSessions"] == 0
    assert unknown.json()["running"] == []


async def test_dashboard_limit_bounds_recent(
    seeded: Any, session_factory: Any, build_harness: Any
) -> None:
    # Given two sessions in the workspace
    async with session_factory() as session:
        await _add_second_session(session, seeded)
    harness: ApiHarness = await build_harness(user_id=seeded.user_id)

    # When the dashboard asks for a single history entry
    response = await harness.client.get("/api/dashboard", params={"limit": 1})

    # Then exactly that many recent sessions are returned
    assert response.status_code == 200
    assert len(response.json()["recent"]) == 1


async def _add_second_session(session: AsyncSession, seeded: Any) -> None:
    """Insert one more session for the seeded workspace."""
    from slopolis_db.models import Repository, User, Workspace

    workspace = await session.get(Workspace, seeded.workspace_id)
    user = await session.get(User, seeded.user_id)
    repository = await session.get(Repository, seeded.repository_id)
    assert workspace is not None and user is not None and repository is not None
    await seed_session(
        session,
        workspace=workspace,
        user=user,
        repository=repository,
        number=8,
        status="done",
        title="Second review",
    )
