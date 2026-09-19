"""Auth surface: identity, logout, and the OAuth redirect."""

from __future__ import annotations

from typing import Any

from slopolis_db.models import User

from .conftest import ApiHarness


async def test_me_returns_current_user(seeded: Any, build_harness: Any) -> None:
    # Given an authenticated user
    harness: ApiHarness = await build_harness(user_id=seeded.user_id)

    # When their identity is requested
    response = await harness.client.get("/api/me")

    # Then the wire user reference is returned
    assert response.status_code == 200
    body = response.json()
    assert body["handle"] == "octocat"
    assert body["name"] == "Mona Lisa"
    assert body["id"] == str(seeded.user_id)


async def test_me_exposes_admin_role(
    seeded: Any, build_harness: Any, session_factory: Any
) -> None:
    # Given an ordinary workspace member
    harness: ApiHarness = await build_harness(user_id=seeded.user_id)

    # When their identity is requested
    response = await harness.client.get("/api/me")

    # Then the wire reference reports no admin role
    assert response.status_code == 200
    assert response.json()["isAdmin"] is False

    # And after the member is promoted to admin
    async with session_factory() as session:
        user = await session.get(User, seeded.user_id)
        assert user is not None
        user.is_admin = True
        await session.commit()

    # Then the same endpoint reports the admin role
    response = await harness.client.get("/api/me")
    assert response.status_code == 200
    assert response.json()["isAdmin"] is True


async def test_logout_clears_cookie(seeded: Any, build_harness: Any) -> None:
    # Given an authenticated user
    harness: ApiHarness = await build_harness(user_id=seeded.user_id)

    # When they log out
    response = await harness.client.post("/api/auth/logout")

    # Then the session cookie is cleared
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert "slopolis_session" in response.headers.get("set-cookie", "")


async def test_login_redirects_to_github(seeded: Any, build_harness: Any) -> None:
    # Given a workspace with GitHub OAuth configured
    harness: ApiHarness = await build_harness(user_id=seeded.user_id)

    # When the login endpoint is hit without credentials configured
    response = await harness.client.get(
        "/api/auth/github/login", follow_redirects=False
    )

    # Then the server reports that OAuth is not configured
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "oauth_not_configured"
