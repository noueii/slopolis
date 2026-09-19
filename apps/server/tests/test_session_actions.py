"""Session detail, rename, and cancel."""

from __future__ import annotations

import uuid
from typing import Any

from .conftest import ApiHarness


async def test_get_session_returns_targets(
    seeded: Any, build_harness: Any
) -> None:
    # Given a seeded session
    harness: ApiHarness = await build_harness(user_id=seeded.user_id)

    # When it is fetched by id
    response = await harness.client.get(f"/api/sessions/{seeded.session_id}")

    # Then the full session with its target is returned
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(seeded.session_id)
    assert body["status"] == "queued"
    assert body["prompt"] == "Focus on security"
    assert body["targets"][0]["headBranch"] == "fix/branch"


async def test_get_unknown_session_is_404(
    seeded: Any, build_harness: Any
) -> None:
    # Given a workspace without the requested session
    harness: ApiHarness = await build_harness(user_id=seeded.user_id)

    # When an unknown id is fetched
    response = await harness.client.get(f"/api/sessions/{uuid.uuid4()}")

    # Then the standard 404 envelope is returned
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "session_not_found"


async def test_patch_session_renames(seeded: Any, build_harness: Any) -> None:
    # Given a seeded session
    harness: ApiHarness = await build_harness(user_id=seeded.user_id)

    # When its title is patched
    response = await harness.client.patch(
        f"/api/sessions/{seeded.session_id}", json={"title": "Renamed review"}
    )

    # Then the new title is persisted in the response
    assert response.status_code == 200
    assert response.json()["title"] == "Renamed review"


async def test_patch_session_requires_a_value(
    seeded: Any, build_harness: Any
) -> None:
    # Given a seeded session
    harness: ApiHarness = await build_harness(user_id=seeded.user_id)

    # When an empty patch is sent
    response = await harness.client.patch(
        f"/api/sessions/{seeded.session_id}", json={"title": "   "}
    )

    # Then a 422 title error is returned
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "title_required"


async def test_cancel_session_then_conflict(
    seeded: Any, build_harness: Any
) -> None:
    # Given a queued session
    harness: ApiHarness = await build_harness(user_id=seeded.user_id)

    # When it is cancelled and then cancelled again
    first = await harness.client.post(f"/api/sessions/{seeded.session_id}/cancel")
    second = await harness.client.post(f"/api/sessions/{seeded.session_id}/cancel")

    # Then the first wins and the second reports a conflict
    assert first.status_code == 200
    assert first.json()["status"] == "cancelled"
    assert first.json()["targets"][0]["status"] == "cancelled"
    assert first.json()["finishedAt"] is not None
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "session_not_cancellable"
