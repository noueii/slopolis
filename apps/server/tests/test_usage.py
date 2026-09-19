"""Usage attribution endpoint."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from slopolis_db.models import UsageRecord

from .conftest import ApiHarness


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
