"""GitHub user-to-server OAuth for the API.

Implements the three-step browser flow: redirect to GitHub, exchange the
``code`` for an access token, fetch the profile, then upsert the ``User`` (and
its workspace) and set a signed httpOnly cookie. Logout clears the cookie.

The round trip is bound to the browser that started it: login mints a single-use
``state``, sends it to GitHub, and parks it in a short-lived signed cookie scoped
to the auth routes; the callback exchanges a ``code`` only when it comes back
with that state. A visitor who never started a sign-in cannot plant one.

The GitHub HTTP calls go through :class:`GitHubOAuthClient`, a narrow port over
``httpx.AsyncClient`` so tests can substitute a fake without network access.
"""

from __future__ import annotations

import datetime as dt
import hmac
import secrets
import urllib.parse
from typing import Annotated, cast

import httpx
from fastapi import APIRouter, Cookie, Depends, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from itsdangerous import BadSignature, URLSafeTimedSerializer
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import AppSettings
from app.deps import (
    AppSettingsDep,
    CurrentUserDep,
    DbSessionDep,
    OptionalVaultDep,
    encode_user_id,
)
from app.errors import ApiError
from app.schemas import MeResponse
from app.serializers import workspace_ref
from slopolis_core.vault import SecretVault
from slopolis_db.models import User, Workspace

__all__ = ["GitHubOAuthClient", "me_router", "router"]

_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
_TOKEN_URL = "https://github.com/login/oauth/access_token"
_USER_URL = "https://api.github.com/user"
_SCOPES = "read:user user:email repo"

# The login redirect parks its single-use ``state`` in this cookie, scoped to the
# auth routes so it never travels with ordinary API calls and only has to survive
# one trip through GitHub. It is signed with the session cookie's key.
_STATE_COOKIE = "slopolis_oauth_state"
_STATE_COOKIE_PATH = "/api/auth"
_STATE_TTL_SECONDS = 600
_STATE_SALT = "oauth-state"


class GitHubOAuthClient:
    """Narrow HTTP surface for the OAuth exchange, over ``httpx``."""

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client or httpx.AsyncClient(timeout=30.0)

    async def aclose(self) -> None:
        """Close the underlying connection pool."""
        await self._client.aclose()

    async def exchange_code(
        self, *, client_id: str, client_secret: str, code: str
    ) -> str:
        """Exchange an OAuth ``code`` for a user access token."""
        response = await self._client.post(
            _TOKEN_URL,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
            },
            headers={"Accept": "application/json"},
        )
        if response.status_code >= 400:
            raise ApiError(502, "oauth_exchange_failed", "GitHub rejected the sign-in.")
        payload: object = response.json()
        if not isinstance(payload, dict):
            raise ApiError(502, "oauth_exchange_failed", "GitHub returned an invalid response.")
        token = cast("dict[str, object]", payload).get("access_token")
        if not isinstance(token, str) or not token:
            raise ApiError(502, "oauth_exchange_failed", "GitHub returned no access token.")
        return token

    async def fetch_user(self, *, token: str) -> GitHubProfile:
        """Fetch the authenticated user's profile."""
        response = await self._client.get(
            _USER_URL,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
        )
        if response.status_code >= 400:
            raise ApiError(502, "oauth_profile_failed", "Could not read your GitHub profile.")
        payload: object = response.json()
        if not isinstance(payload, dict):
            raise ApiError(502, "oauth_profile_failed", "GitHub returned an invalid profile.")
        data = cast("dict[str, object]", payload)
        raw_id = data.get("id")
        login = data.get("login")
        if not isinstance(raw_id, int) or not isinstance(login, str):
            raise ApiError(502, "oauth_profile_failed", "GitHub returned an invalid profile.")
        name = data.get("name")
        avatar = data.get("avatar_url")
        return GitHubProfile(
            id=raw_id,
            login=login,
            name=name if isinstance(name, str) and name else login,
            avatar_url=avatar if isinstance(avatar, str) else None,
        )


class GitHubProfile(BaseModel):
    """The subset of the GitHub user object the app persists."""

    model_config = ConfigDict(extra="ignore")

    id: int
    login: str
    name: str
    avatar_url: str | None = None


def get_oauth_client(request: Request) -> GitHubOAuthClient:
    """Return the app-owned OAuth client (overridable in tests)."""
    client: GitHubOAuthClient | None = getattr(request.app.state, "oauth_client", None)
    if client is None:
        raise ApiError(503, "oauth_not_configured", "GitHub OAuth is not configured.")
    return client


OAuthClientDep = Annotated[GitHubOAuthClient, Depends(get_oauth_client)]

router = APIRouter(prefix="/auth", tags=["auth"])
me_router = APIRouter(tags=["auth"])


def _require_credentials(settings: AppSettings) -> tuple[str, str]:
    """Return ``(client_id, client_secret)`` or fail with a config error."""
    core = settings.core
    if not core.github_client_id or not core.github_client_secret:
        raise ApiError(
            503,
            "oauth_not_configured",
            "GitHub OAuth credentials are not configured.",
        )
    return core.github_client_id, core.github_client_secret


def _sign_state(state: str, settings: AppSettings) -> str:
    """Sign an OAuth ``state`` for the cookie, with the session cookie's key."""
    return URLSafeTimedSerializer(settings.cookie_secret, salt=_STATE_SALT).dumps(state)


def _read_state(cookie: str, settings: AppSettings) -> str | None:
    """Return the ``state`` a signed cookie carries, or ``None`` when unusable."""
    serializer = URLSafeTimedSerializer(settings.cookie_secret, salt=_STATE_SALT)
    try:
        loaded: object = serializer.loads(cookie, max_age=_STATE_TTL_SECONDS)
    except BadSignature:
        return None
    return loaded if isinstance(loaded, str) else None


def _state_matches(state: str | None, cookie: str | None, settings: AppSettings) -> bool:
    """Whether a callback presents the state the browser was given.

    The comparison is constant-time (and over bytes, so a non-ASCII guess is
    simply wrong rather than an error), leaving nothing to narrow a state down.
    """
    if not state or not cookie:
        return False
    expected = _read_state(cookie, settings)
    if expected is None:
        return False
    return hmac.compare_digest(state.encode(), expected.encode())


def _invalid_state_error() -> ApiError:
    """Refuse a callback whose ``state`` cannot be matched, dropping the cookie.

    The cookie has to go on this path too, and the app-level error handler only
    renders the envelope — so the deletion header is built by a throwaway
    response and carried on the error.
    """
    probe = Response()
    probe.delete_cookie(_STATE_COOKIE, path=_STATE_COOKIE_PATH)
    return ApiError(
        400,
        "invalid_oauth_state",
        "The sign-in attempt could not be verified; start again.",
        headers={"set-cookie": probe.headers["set-cookie"]},
    )


@router.get("/github/login")
async def github_login(settings: AppSettingsDep) -> RedirectResponse:
    """Redirect the browser to GitHub's OAuth consent screen, binding the attempt."""
    client_id, _secret = _require_credentials(settings)
    state = secrets.token_urlsafe(32)
    query = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "scope": _SCOPES,
            "redirect_uri": f"{settings.core.app_url}/api/auth/github/callback",
            "state": state,
        }
    )
    response = RedirectResponse(f"{_AUTHORIZE_URL}?{query}", status_code=302)
    response.set_cookie(
        key=_STATE_COOKIE,
        value=_sign_state(state, settings),
        max_age=_STATE_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        secure=settings.core.app_url.startswith("https"),
        path=_STATE_COOKIE_PATH,
    )
    return response


@router.get("/github/callback")
async def github_callback(
    code: str,
    db: DbSessionDep,
    settings: AppSettingsDep,
    oauth: OAuthClientDep,
    vault: OptionalVaultDep,
    state: str | None = None,
    state_cookie: Annotated[str | None, Cookie(alias=_STATE_COOKIE)] = None,
) -> RedirectResponse:
    """Exchange the code, upsert the user, and set the signed session cookie.

    A ``code`` is only worth exchanging when it comes back with the ``state``
    this browser asked for; the cookie is consumed on both outcomes, which is
    what makes a replay of a stolen state fail.
    """
    client_id, client_secret = _require_credentials(settings)
    if not _state_matches(state, state_cookie, settings):
        raise _invalid_state_error()
    token = await oauth.exchange_code(
        client_id=client_id, client_secret=client_secret, code=code
    )
    profile = await oauth.fetch_user(token=token)
    user = await _upsert_user(db, profile)
    _store_github_token(user, token, vault)
    await db.commit()

    response = RedirectResponse(settings.core.app_url, status_code=302)
    response.set_cookie(
        key=settings.cookie_name,
        value=encode_user_id(user.id, settings),
        max_age=settings.session_lifetime_seconds,
        httponly=True,
        samesite="lax",
        secure=settings.core.app_url.startswith("https"),
        path="/",
    )
    response.delete_cookie(_STATE_COOKIE, path=_STATE_COOKIE_PATH)
    return response


@router.post("/logout")
async def logout(settings: AppSettingsDep) -> JSONResponse:
    """Clear the session cookie."""
    response = JSONResponse({"ok": True})
    response.delete_cookie(settings.cookie_name, path="/")
    return response


@me_router.get("/me")
async def me(user: CurrentUserDep, db: DbSessionDep) -> MeResponse:
    """Return the authenticated user plus the workspace they act in, if any.

    The workspace is read here rather than through a lazy relationship: the wire
    mapper stays a pure function, and a workspace-less account simply gets
    ``workspace: null`` — the signal the onboarding gate keys off.
    """
    workspace = await db.get(Workspace, user.workspace_id) if user.workspace_id else None
    return _to_user_ref(user, workspace)


def _store_github_token(user: User, token: str, vault: SecretVault | None) -> None:
    """Seal the user's access token for later repo-access checks (spec 10.1).

    A deployment with no ``ENCRYPTION_KEY`` still signs in — it has nothing to
    seal with, so it stores no token and every check for this account is then
    unverifiable (10.8 §Access). The token is never logged and never returned.
    """
    if vault is None:
        return
    user.encrypted_github_token = vault.seal(token)
    user.token_updated_at = dt.datetime.now(dt.UTC)


async def _upsert_user(db: AsyncSession, profile: GitHubProfile) -> User:
    """Find or create the user; a new account starts with no workspace.

    Signing in is not the same as belonging somewhere: the account is created
    here, and ``POST /api/workspaces`` (or an invitation) decides the tenant.
    """
    existing = await db.scalar(select(User).where(User.github_id == profile.id))
    if existing is not None:
        existing.handle = profile.login
        existing.name = profile.name
        existing.avatar_url = profile.avatar_url
        return existing

    user = User(
        workspace_id=None,
        github_id=profile.id,
        handle=profile.login,
        name=profile.name,
        avatar_url=profile.avatar_url,
        is_admin=False,
    )
    db.add(user)
    await db.flush()
    return user


def _to_user_ref(user: User, workspace: Workspace | None = None) -> MeResponse:
    """Map an ORM user (and its workspace) onto the identity response."""
    return MeResponse(
        id=str(user.id),
        handle=user.handle,
        name=user.name,
        avatar_url=user.avatar_url,
        is_admin=user.is_admin,
        workspace=workspace_ref(workspace),
    )
