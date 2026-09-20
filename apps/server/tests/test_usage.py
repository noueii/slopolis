"""Usage attribution endpoint."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select

from slopolis_db.models import (
    Repository,
    SessionTarget,
    UsageRecord,
    User,
    Workspace,
)

from .conftest import ApiHarness, seed_session


async def test_usage_rolls_up_records(
    seeded: Any, session_factory: Any, build_harness: Any
) -> None:
    # Given two usage records attributed to the seeded session
    async with session_factory() as session:
        session.add_all(
            [
                UsageRecord(
                    workspace_id=seeded.workspace_id,
                    model_id="claude-sonnet-4",
                    provider="Anthropic",
                    prompt_tokens=100,
                    completion_tokens=20,
                    total_tokens=120,
                    cost_usd=Decimal("0.010000"),
                ),
                UsageRecord(
                    workspace_id=seeded.workspace_id,
                    model_id="gpt-4o",
                    provider="OpenAI",
                    prompt_tokens=50,
                    completion_tokens=10,
                    total_tokens=60,
                    cost_usd=Decimal("0.002000"),
                ),
            ]
        )
        await session.commit()
    harness: ApiHarness = await build_harness(user_id=seeded.user_id)

    # When usage is requested
    response = await harness.client.get("/api/usage")

    # Then totals and per-model breakdowns are returned
    assert response.status_code == 200
    body = response.json()
    assert body["totalTokens"] == 180
    assert body["totalCostUsd"] == 0.012
    assert {row["key"] for row in body["byModel"]} == {"claude-sonnet-4", "gpt-4o"}
    assert len(body["series"]) >= 1


async def test_usage_empty_workspace(
    seeded: Any, build_harness: Any
) -> None:
    # Given no usage records
    harness: ApiHarness = await build_harness(user_id=seeded.user_id)

    # When usage is requested
    response = await harness.client.get("/api/usage")

    # Then zeroed totals and empty breakdowns are returned
    body = response.json()
    assert body["totalTokens"] == 0
    assert body["totalCostUsd"] == 0
    assert body["byModel"] == []
    assert body["byRepository"] == []
    assert body["byUser"] == []
    assert body["series"] == []


async def test_usage_ignores_other_workspaces(
    seeded: Any, session_factory: Any, build_harness: Any
) -> None:
    # Given a usage record owned by a different workspace
    async with session_factory() as session:
        session.add(
            UsageRecord(
                workspace_id=uuid.uuid4(),
                model_id="gpt-4o",
                provider="OpenAI",
                prompt_tokens=10,
                completion_tokens=5,
                total_tokens=15,
                cost_usd=Decimal("0.001000"),
            )
        )
        await session.commit()
    harness: ApiHarness = await build_harness(user_id=seeded.user_id)

    # When usage is requested
    response = await harness.client.get("/api/usage")

    # Then the foreign workspace's record is excluded
    assert response.json()["totalTokens"] == 0


async def test_usage_attributes_records_to_the_session_user(
    seeded: Any, session_factory: Any, build_harness: Any
) -> None:
    # Given a usage record on the session octocat triggered
    async with session_factory() as session:
        session.add(
            UsageRecord(
                workspace_id=seeded.workspace_id,
                session_id=seeded.session_id,
                model_id="claude-sonnet-4",
                provider="Anthropic",
                prompt_tokens=100,
                completion_tokens=20,
                total_tokens=120,
                cost_usd=Decimal("0.010000"),
            )
        )
        await session.commit()
    harness: ApiHarness = await build_harness(user_id=seeded.user_id)

    # When usage is requested
    response = await harness.client.get("/api/usage")

    # Then it is attributed to that user's handle, keyed by their id
    assert response.json()["byUser"] == [
        {
            "key": str(seeded.user_id),
            "label": "octocat",
            "tokens": 120,
            "costUsd": 0.01,
            "sessions": 1,
        }
    ]


async def test_usage_attributes_target_only_records_through_their_session(
    seeded: Any, session_factory: Any, build_harness: Any
) -> None:
    # Given a record that names only its target, as the worker writes them
    async with session_factory() as session:
        target = await session.scalar(
            select(SessionTarget).where(SessionTarget.session_id == seeded.session_id)
        )
        assert target is not None
        session.add(
            UsageRecord(
                workspace_id=seeded.workspace_id,
                target_id=target.id,
                model_id="claude-sonnet-4",
                provider="Anthropic",
                prompt_tokens=100,
                completion_tokens=20,
                total_tokens=120,
                cost_usd=Decimal("0.010000"),
            )
        )
        await session.commit()
    harness: ApiHarness = await build_harness(user_id=seeded.user_id)

    # When usage is requested
    response = await harness.client.get("/api/usage")

    # Then the target's session attributes it to the triggering user
    assert response.json()["byUser"] == [
        {
            "key": str(seeded.user_id),
            "label": "octocat",
            "tokens": 120,
            "costUsd": 0.01,
            "sessions": 1,
        }
    ]


async def test_usage_separates_users(
    seeded: Any, session_factory: Any, build_harness: Any
) -> None:
    # Given a second workspace member with a session of their own
    async with session_factory() as session:
        workspace = await session.get(Workspace, seeded.workspace_id)
        repository = await session.get(Repository, seeded.repository_id)
        assert workspace is not None
        assert repository is not None
        other = User(
            workspace_id=workspace.id,
            github_id=1002,
            handle="hubot",
            name="Hub Ot",
        )
        session.add(other)
        await session.flush()
        second = await seed_session(
            session, workspace=workspace, user=other, repository=repository, number=8
        )
        # And one usage record per session
        session.add_all(
            [
                UsageRecord(
                    workspace_id=seeded.workspace_id,
                    session_id=seeded.session_id,
                    model_id="claude-sonnet-4",
                    provider="Anthropic",
                    prompt_tokens=100,
                    completion_tokens=20,
                    total_tokens=120,
                    cost_usd=Decimal("0.010000"),
                ),
                UsageRecord(
                    workspace_id=seeded.workspace_id,
                    session_id=second.id,
                    model_id="gpt-4o",
                    provider="OpenAI",
                    prompt_tokens=50,
                    completion_tokens=10,
                    total_tokens=60,
                    cost_usd=Decimal("0.002000"),
                ),
            ]
        )
        await session.commit()
        other_id = other.id
    harness: ApiHarness = await build_harness(user_id=seeded.user_id)

    # When usage is requested
    response = await harness.client.get("/api/usage")

    # Then each user gets their own bucket
    body = response.json()
    assert {row["label"]: row["tokens"] for row in body["byUser"]} == {
        "octocat": 120,
        "hubot": 60,
    }
    assert {row["key"] for row in body["byUser"]} == {
        str(seeded.user_id),
        str(other_id),
    }


async def test_usage_groups_unresolvable_records_as_unattributed(
    seeded: Any, session_factory: Any, build_harness: Any
) -> None:
    # Given a record that names neither a session nor a target
    async with session_factory() as session:
        session.add(
            UsageRecord(
                workspace_id=seeded.workspace_id,
                model_id="gpt-4o",
                provider="OpenAI",
                prompt_tokens=50,
                completion_tokens=10,
                total_tokens=60,
                cost_usd=Decimal("0.002000"),
            )
        )
        await session.commit()
    harness: ApiHarness = await build_harness(user_id=seeded.user_id)

    # When usage is requested
    response = await harness.client.get("/api/usage")

    # Then it is reported as unattributed rather than dropped
    assert response.json()["byUser"] == [
        {
            "key": "unattributed",
            "label": "Unattributed",
            "tokens": 60,
            "costUsd": 0.002,
            "sessions": 0,
        }
    ]


async def test_usage_by_user_sums_to_totals(
    seeded: Any, session_factory: Any, build_harness: Any
) -> None:
    # Given records for the triggering user, a second user, and no user at all
    async with session_factory() as session:
        workspace = await session.get(Workspace, seeded.workspace_id)
        repository = await session.get(Repository, seeded.repository_id)
        assert workspace is not None
        assert repository is not None
        other = User(
            workspace_id=workspace.id,
            github_id=1002,
            handle="hubot",
            name="Hub Ot",
        )
        session.add(other)
        await session.flush()
        second = await seed_session(
            session, workspace=workspace, user=other, repository=repository, number=8
        )
        session.add_all(
            [
                UsageRecord(
                    workspace_id=seeded.workspace_id,
                    session_id=seeded.session_id,
                    model_id="claude-sonnet-4",
                    provider="Anthropic",
                    prompt_tokens=100,
                    completion_tokens=20,
                    total_tokens=120,
                    cost_usd=Decimal("0.010000"),
                ),
                UsageRecord(
                    workspace_id=seeded.workspace_id,
                    session_id=second.id,
                    model_id="gpt-4o",
                    provider="OpenAI",
                    prompt_tokens=50,
                    completion_tokens=10,
                    total_tokens=60,
                    cost_usd=Decimal("0.002000"),
                ),
                UsageRecord(
                    workspace_id=seeded.workspace_id,
                    model_id="gpt-4o-mini",
                    provider="OpenAI",
                    prompt_tokens=10,
                    completion_tokens=5,
                    total_tokens=15,
                    cost_usd=Decimal("0.000300"),
                ),
            ]
        )
        await session.commit()
    harness: ApiHarness = await build_harness(user_id=seeded.user_id)

    # When usage is requested
    response = await harness.client.get("/api/usage")

    # Then every record lands in exactly one bucket, so the shares add up
    body = response.json()
    assert len(body["byUser"]) == 3
    assert sum(row["tokens"] for row in body["byUser"]) == body["totalTokens"]
    assert sum(row["costUsd"] for row in body["byUser"]) == pytest.approx(
        body["totalCostUsd"], rel=0, abs=1e-9
    )
