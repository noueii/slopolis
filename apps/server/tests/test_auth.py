"""Auth surface: identity, logout, and the OAuth round trip.

The sign-in is bound to the browser that started it, so the tests around the
callback care less about the happy path than about what a *code* is allowed to
do when it arrives without the state that browser was given.
"""

from __future__ import annotations

import base64
import re
import urllib.parse
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from app.config import get_app_settings
from app.deps import get_vault
from itsdangerous import URLSafeTimedSerializer
from sqlalchemy import select

from slopolis_core.settings import get_settings
from slopolis_db.models import User

from .conftest import ApiHarness, FakeOAuthClient

AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
APP_URL = "http://localhost:8400"
STATE_COOKIE = "slopolis_oauth_state"
SESSION_COOKIE = "slopolis_session"
#: The GitHub account :class:`FakeOAuthClient` reports, i.e. the one the callback creates.
PROFILE_ID = 9001
_MASTER_KEY = base64.b64encode(b"auth-tests-master-key-material!!").decode()


def _reset_memos() -> None:
    """Clear the cached settings and vault after an environment change."""
    get_settings.cache_clear()
    get_vault.cache_clear()


@pytest.fixture
def vault_key(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Give the deployment an ``ENCRYPTION_KEY`` for one test."""
    monkeypatch.setenv("ENCRYPTION_KEY", _MASTER_KEY)
    _reset_memos()
    yield
    _reset_memos()


@pytest.fixture
def vault_absent(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """A deployment with no ``ENCRYPTION_KEY``: the degraded sign-in mode."""
    monkeypatch.setenv("ENCRYPTION_KEY", "")
    _reset_memos()
    yield
    _reset_memos()


@pytest.fixture
def oauth_credentials(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Give the deployment a user-to-server OAuth client for one test.

    The core settings are cached process-wide, so the cache is dropped on both
    sides of the environment patch: every other test keeps the unconfigured
    default that the login route reports as 503.
    """
    monkeypatch.setenv("GITHUB_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "test-client-secret")
    try:
        get_settings.cache_clear()
        yield
    finally:
        get_settings.cache_clear()


def state_cookie_headers(response: httpx.Response) -> list[str]:
    """Every ``Set-Cookie`` header the response issued for the state cookie."""
    return [
        header
        for header in response.headers.get_list("set-cookie")
        if header.startswith(f"{STATE_COOKIE}=")
    ]


def state_cookie_value(response: httpx.Response) -> str:
    """The value the response set for the state cookie."""
    headers = state_cookie_headers(response)
    assert len(headers) == 1, headers
    return headers[0].split("=", 1)[1].split(";", 1)[0]


def state_from(location: str) -> str:
    """The ``state`` a login redirect hands to GitHub."""
    return urllib.parse.parse_qs(urllib.parse.urlsplit(location).query)["state"][0]


def assert_refused(response: httpx.Response) -> None:
    """Refuse an unbound callback: 400 with the code, no session, cookie consumed."""
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_oauth_state"
    assert not [
        header
        for header in response.headers.get_list("set-cookie")
        if header.startswith(f"{SESSION_COOKIE}=")
    ]
    consumed = state_cookie_headers(response)
    assert len(consumed) == 1, consumed
    assert "Max-Age=0" in consumed[0]


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


async def test_login_reports_an_unconfigured_deployment(
    seeded: Any, build_harness: Any
) -> None:
    # Given a deployment without OAuth credentials configured
    harness: ApiHarness = await build_harness(user_id=seeded.user_id)

    # When the login endpoint is hit
    response = await harness.client.get(
        "/api/auth/github/login", follow_redirects=False
    )

    # Then the server reports that OAuth is not configured, and binds no attempt
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "oauth_not_configured"
    assert state_cookie_headers(response) == []


async def test_login_binds_the_attempt_to_this_browser(
    oauth_credentials: None, seeded: Any, build_harness: Any
) -> None:
    # Given a deployment with OAuth credentials
    harness: ApiHarness = await build_harness(user_id=seeded.user_id)

    # When the browser starts the sign-in
    response = await harness.client.get(
        "/api/auth/github/login", follow_redirects=False
    )

    # Then it goes to GitHub with the authorize parameters it always sent
    assert response.status_code == 302
    assert response.headers["location"].startswith(f"{AUTHORIZE_URL}?")
    query = urllib.parse.parse_qs(
        urllib.parse.urlsplit(response.headers["location"]).query
    )
    assert query["client_id"] == ["test-client-id"]
    assert query["scope"] == ["read:user user:email repo"]
    assert query["redirect_uri"] == [f"{APP_URL}/api/auth/github/callback"]

    # And a fresh single-use state rides along in a signed, httpOnly cookie that
    # only the auth routes ever see
    state = query["state"][0]
    assert re.fullmatch(r"[A-Za-z0-9_-]{43}", state)
    headers = state_cookie_headers(response)
    assert len(headers) == 1
    assert "Path=/api/auth" in headers[0]
    assert "Max-Age=600" in headers[0]
    assert "HttpOnly" in headers[0]
    assert "SameSite=lax" in headers[0]
    cookie = state_cookie_value(response)
    assert cookie != state
    signer = URLSafeTimedSerializer(get_app_settings().cookie_secret, salt="oauth-state")
    assert signer.loads(cookie, max_age=600) == state


async def test_callback_with_the_matching_state_signs_in(
    oauth_credentials: None, seeded: Any, build_harness: Any, session_factory: Any
) -> None:
    # Given a browser that started the sign-in
    fake = FakeOAuthClient()
    harness: ApiHarness = await build_harness(user_id=seeded.user_id, oauth_client=fake)
    login = await harness.client.get("/api/auth/github/login", follow_redirects=False)

    # When GitHub sends it back with the state it was given
    response = await harness.client.get(
        f"/api/auth/github/callback?code=the-code&state={state_from(login.headers['location'])}",
        follow_redirects=False,
    )

    # Then the browser lands in the app with a session
    assert response.status_code == 302
    assert response.headers["location"] == APP_URL
    assert any(
        header.startswith(f"{SESSION_COOKIE}=")
        for header in response.headers.get_list("set-cookie")
    )
    assert fake.codes == ["the-code"]
    assert fake.tokens == ["gho_test_token"]

    # And the account GitHub described now exists, with no workspace yet
    async with session_factory() as session:
        user = await session.scalar(select(User).where(User.github_id == PROFILE_ID))
    assert user is not None
    assert user.handle == "newcomer"
    assert user.workspace_id is None

    # And the state is spent: the browser no longer holds it
    assert state_cookie_headers(response)
    assert "Max-Age=0" in state_cookie_headers(response)[0]
    assert harness.client.cookies.get(STATE_COOKIE) is None


async def test_callback_without_a_state_parameter_is_refused(
    oauth_credentials: None, seeded: Any, build_harness: Any
) -> None:
    # Given a browser that started a sign-in and holds its state cookie
    fake = FakeOAuthClient()
    harness: ApiHarness = await build_harness(user_id=seeded.user_id, oauth_client=fake)
    await harness.client.get("/api/auth/github/login", follow_redirects=False)

    # When a code comes back without the state GitHub was told to echo
    response = await harness.client.get(
        "/api/auth/github/callback?code=stolen-code", follow_redirects=False
    )

    # Then it is refused and the attempt is over
    assert_refused(response)
    assert fake.codes == []


async def test_callback_without_a_state_cookie_is_refused(
    oauth_credentials: None, seeded: Any, build_harness: Any, session_factory: Any
) -> None:
    # Given a fresh browser that never started a sign-in
    fake = FakeOAuthClient()
    harness: ApiHarness = await build_harness(user_id=seeded.user_id, oauth_client=fake)

    # When a code and a guess at the state are planted in it
    response = await harness.client.get(
        "/api/auth/github/callback?code=stolen-code&state=guessed-state",
        follow_redirects=False,
    )

    # Then the code is never exchanged and no account is created from it
    assert_refused(response)
    assert fake.codes == []
    assert fake.tokens == []
    async with session_factory() as session:
        assert (
            await session.scalar(select(User).where(User.github_id == PROFILE_ID))
        ) is None


async def test_callback_with_a_state_from_another_attempt_is_refused(
    oauth_credentials: None, seeded: Any, build_harness: Any
) -> None:
    # Given one browser that started a sign-in
    fake = FakeOAuthClient()
    started: ApiHarness = await build_harness(user_id=seeded.user_id, oauth_client=fake)
    login = await started.client.get("/api/auth/github/login", follow_redirects=False)
    cookie = state_cookie_value(login)

    # When its cookie is planted in a second browser next to another state
    other: ApiHarness = await build_harness(user_id=seeded.user_id, oauth_client=fake)
    other.client.cookies.set(STATE_COOKIE, cookie)
    response = await other.client.get(
        "/api/auth/github/callback?code=stolen-code&state=some-other-state",
        follow_redirects=False,
    )

    # Then the mismatch is refused
    assert_refused(response)
    assert fake.codes == []


async def test_callback_with_a_tampered_cookie_is_refused(
    oauth_credentials: None, seeded: Any, build_harness: Any
) -> None:
    # Given a browser carrying a state cookie that is not one of ours
    fake = FakeOAuthClient()
    harness: ApiHarness = await build_harness(user_id=seeded.user_id, oauth_client=fake)
    harness.client.cookies.set(STATE_COOKIE, "tampered")

    # When a code arrives with it
    response = await harness.client.get(
        "/api/auth/github/callback?code=stolen-code&state=tampered",
        follow_redirects=False,
    )

    # Then the unreadable cookie is refused like any other mismatch
    assert_refused(response)
    assert fake.codes == []


async def test_callback_with_a_non_ascii_state_is_refused(
    oauth_credentials: None, seeded: Any, build_harness: Any
) -> None:
    # Given a browser that started a sign-in
    fake = FakeOAuthClient()
    harness: ApiHarness = await build_harness(user_id=seeded.user_id, oauth_client=fake)
    login = await harness.client.get("/api/auth/github/login", follow_redirects=False)
    assert state_from(login.headers["location"])

    # When the state comes back with characters that are not ASCII
    response = await harness.client.get(
        "/api/auth/github/callback",
        params={"code": "stolen-code", "state": "staté"},
        follow_redirects=False,
    )

    # Then it is an ordinary refusal rather than a crash
    assert_refused(response)
    assert fake.codes == []


async def test_callback_replay_is_refused(
    oauth_credentials: None, seeded: Any, build_harness: Any
) -> None:
    # Given a sign-in that already completed
    fake = FakeOAuthClient()
    harness: ApiHarness = await build_harness(user_id=seeded.user_id, oauth_client=fake)
    login = await harness.client.get("/api/auth/github/login", follow_redirects=False)
    path = (
        "/api/auth/github/callback?code=the-code"
        f"&state={state_from(login.headers['location'])}"
    )
    first = await harness.client.get(path, follow_redirects=False)
    assert first.status_code == 302

    # When the same callback is replayed with the state it consumed
    response = await harness.client.get(path, follow_redirects=False)

    # Then the second attempt is refused: the cookie is what made it single-use
    assert_refused(response)
    assert fake.codes == ["the-code"]


async def test_callback_without_a_code_is_a_validation_error(
    oauth_credentials: None, seeded: Any, build_harness: Any
) -> None:
    # Given a browser that started a sign-in
    fake = FakeOAuthClient()
    harness: ApiHarness = await build_harness(user_id=seeded.user_id, oauth_client=fake)
    login = await harness.client.get("/api/auth/github/login", follow_redirects=False)

    # When GitHub comes back with the state but no code
    response = await harness.client.get(
        f"/api/auth/github/callback?state={state_from(login.headers['location'])}",
        follow_redirects=False,
    )

    # Then the missing code is still a validation error, and nothing is exchanged
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert fake.codes == []


async def test_callback_reports_an_unconfigured_deployment(
    seeded: Any, build_harness: Any
) -> None:
    # Given a deployment with no OAuth credentials wired
    harness: ApiHarness = await build_harness(user_id=seeded.user_id)

    # When the callback arrives
    response = await harness.client.get(
        "/api/auth/github/callback?code=the-code&state=some-state",
        follow_redirects=False,
    )

    # Then the operator gets the configuration error, as before
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "oauth_not_configured"


async def test_callback_keeps_the_users_token_sealed(
    oauth_credentials: None,
    vault_key: None,
    seeded: Any,
    build_harness: Any,
    session_factory: Any,
) -> None:
    # Given a deployment with a vault and a browser that started the sign-in
    fake = FakeOAuthClient()
    harness: ApiHarness = await build_harness(user_id=seeded.user_id, oauth_client=fake)
    login = await harness.client.get("/api/auth/github/login", follow_redirects=False)

    # When GitHub sends it back with the state it was given
    response = await harness.client.get(
        f"/api/auth/github/callback?code=the-code&state={state_from(login.headers['location'])}",
        follow_redirects=False,
    )

    # Then the sign-in succeeds, the access token is sealed for later repo-access
    # checks (spec 10.1), and the token itself is nowhere in the response
    assert response.status_code == 302
    assert fake.tokens == ["gho_test_token"]
    assert "gho_test_token" not in response.text
    assert "gho_test_token" not in response.headers["location"]

    async with session_factory() as session:
        user = await session.scalar(select(User).where(User.github_id == PROFILE_ID))
    assert user is not None
    blob = user.encrypted_github_token
    assert blob is not None
    assert b"gho_test_token" not in blob
    assert get_vault().open(blob) == "gho_test_token"
    assert user.token_updated_at is not None


async def test_callback_without_a_vault_signs_in_and_stores_no_token(
    oauth_credentials: None,
    vault_absent: None,
    seeded: Any,
    build_harness: Any,
    session_factory: Any,
) -> None:
    # Given a browser that started the sign-in on a deployment with no vault
    fake = FakeOAuthClient()
    harness: ApiHarness = await build_harness(user_id=seeded.user_id, oauth_client=fake)
    login = await harness.client.get("/api/auth/github/login", follow_redirects=False)

    # When the callback completes
    response = await harness.client.get(
        f"/api/auth/github/callback?code=the-code&state={state_from(login.headers['location'])}",
        follow_redirects=False,
    )

    # Then sign-in still succeeds, and the account simply holds no token — the
    # degraded mode whose checks are unverifiable (10.8 §Access)
    assert response.status_code == 302
    async with session_factory() as session:
        user = await session.scalar(select(User).where(User.github_id == PROFILE_ID))
    assert user is not None
    assert user.encrypted_github_token is None
    assert user.token_updated_at is None


async def test_callback_refreshes_the_stored_token_on_the_next_sign_in(
    oauth_credentials: None,
    vault_key: None,
    seeded: Any,
    build_harness: Any,
    session_factory: Any,
) -> None:
    # Given an account whose stored token was sealed at a previous sign-in
    fake = FakeOAuthClient()
    harness: ApiHarness = await build_harness(user_id=seeded.user_id, oauth_client=fake)
    first = await harness.client.get("/api/auth/github/login", follow_redirects=False)
    await harness.client.get(
        f"/api/auth/github/callback?code=the-code&state={state_from(first.headers['location'])}",
        follow_redirects=False,
    )
    async with session_factory() as session:
        user = await session.scalar(select(User).where(User.github_id == PROFILE_ID))
        assert user is not None
        stale = user.encrypted_github_token
        stale_at = user.token_updated_at

    # When the same account signs in again
    second = await harness.client.get("/api/auth/github/login", follow_redirects=False)
    response = await harness.client.get(
        f"/api/auth/github/callback?code=the-code&state={state_from(second.headers['location'])}",
        follow_redirects=False,
    )

    # Then the stored token is replaced, so a revoked-then-renewed token heals
    assert response.status_code == 302
    async with session_factory() as session:
        user = await session.scalar(select(User).where(User.github_id == PROFILE_ID))
    assert user is not None
    assert user.encrypted_github_token != stale
    assert user.token_updated_at is not None
    assert user.token_updated_at >= stale_at
    assert get_vault().open(user.encrypted_github_token) == "gho_test_token"
