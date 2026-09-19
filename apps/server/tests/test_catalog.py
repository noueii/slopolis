"""Catalog endpoints: model catalog and review presets."""

from __future__ import annotations

from typing import Any

from .conftest import ApiHarness, seed_workspace


async def test_models_returns_fallback_catalog(
    session_factory: Any, build_harness: Any
) -> None:
    # Given a workspace with no configured model catalog
    async with session_factory() as session:
        _workspace, user, _repository = await seed_workspace(session)
        user_id = user.id

    # When the catalog is requested
    harness: ApiHarness = await build_harness(user_id=user_id)
    response = await harness.client.get("/api/models")

    # Then the fallback catalog is returned with a stable default
    assert response.status_code == 200
    body = response.json()
    assert body["defaultModelId"] == "claude-sonnet-4"
    assert body["defaultProvider"] == "Anthropic"
    assert {model["id"] for model in body["models"]} >= {"gpt-4o", "claude-sonnet-4"}


async def test_presets_returns_builtin_catalog(
    session_factory: Any, build_harness: Any
) -> None:
    # Given a seeded workspace
    async with session_factory() as session:
        _workspace, user, _repository = await seed_workspace(session)
        user_id = user.id

    # When presets are requested
    harness: ApiHarness = await build_harness(user_id=user_id)
    response = await harness.client.get("/api/presets")

    # Then the built-in preset catalog is returned
    assert response.status_code == 200
    body = response.json()
    assert body["defaultPresetId"] == "default"
    assert [preset["id"] for preset in body["presets"]] == [
        "default",
        "security",
        "performance",
        "tests",
    ]
