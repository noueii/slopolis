"""The standard error envelope on 404, 405, and 422 responses."""

from __future__ import annotations

import uuid
from typing import Any

from .conftest import ApiHarness


async def test_404_uses_error_envelope(seeded: Any, build_harness: Any) -> None:
    # Given a request for a resource that does not exist
    harness: ApiHarness = await build_harness(user_id=seeded.user_id)

    # When an unknown session is fetched
    response = await harness.client.get(f"/api/sessions/{uuid.uuid4()}")

    # Then the body is the standard envelope with a code, message, and detail
    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "session_not_found"
    assert error["message"]
    assert error["detail"]


async def test_validation_error_uses_envelope(
    seeded: Any, build_harness: Any
) -> None:
    # Given a request with an out-of-range pagination value
    harness: ApiHarness = await build_harness(user_id=seeded.user_id)

    # When the query fails FastAPI validation
    response = await harness.client.get("/api/sessions", params={"page": 0})

    # Then a 422 wrapped in the standard envelope is returned
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert "page" in error["detail"]


async def test_invalid_body_uses_envelope(seeded: Any, build_harness: Any) -> None:
    # Given a session-create body with an unexpected field
    harness: ApiHarness = await build_harness(user_id=seeded.user_id)

    # When the body is submitted
    response = await harness.client.post(
        "/api/sessions", json={"prUrls": [], "unexpected": True}
    )

    # Then a 422 validation envelope is returned
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


async def test_unknown_route_uses_envelope(
    seeded: Any, build_harness: Any
) -> None:
    # Given a request to a route that does not exist
    harness: ApiHarness = await build_harness(user_id=seeded.user_id)

    # When it is requested
    response = await harness.client.get("/api/does-not-exist")

    # Then the 404 envelope is returned rather than a bare detail body
    assert response.status_code == 404
    assert "error" in response.json()
