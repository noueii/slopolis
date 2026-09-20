"""FastAPI dependencies: DB session, current user, GitHub client, pre-flight.

Every dependency is an ordinary callable so tests can swap it with
``app.dependency_overrides`` without touching Redis, GitHub, or a real
database. In particular ``get_arq_pool`` and ``get_preflight_service`` are the
two seams the test-suite overrides.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from typing import Annotated, Protocol

from fastapi import Cookie, Depends, Request
from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.github import GitHubGatewayAdapter
from app.adapters.workspace import WorkspaceConfigAdapter
from app.config import AppSettings, get_app_settings
from app.errors import ApiError
from slopolis_core.github.client import GitHubClient
from slopolis_core.llm.client import LlmClient
from slopolis_core.preflight.ports import LlmLiveModelCheck
from slopolis_core.preflight.service import PreflightService
from slopolis_db.models import User
from slopolis_db.session import get_db_session

__all__ = [
    "AppSettingsDep",
    "ArqPool",
    "ArqPoolDep",
    "CurrentUserDep",
    "DbSessionDep",
    "GitHubGatewayDep",
    "OptionalUserDep",
    "PreflightServiceDep",
    "WorkspaceIdDep",
    "decode_user_id",
    "encode_user_id",
    "get_arq_pool",
    "get_current_user",
    "get_db",
    "get_github_gateway",
    "get_optional_user",
    "get_preflight_service",
    "get_settings_dep",
    "get_workspace_id",
]


class ArqPool(Protocol):
    """The narrow slice of ``arq.connections.ArqRedis`` the API enqueues through."""

    async def enqueue_job(
        self, function: str, *args: object, **kwargs: object
    ) -> object: ...


async def get_db() -> AsyncGenerator[AsyncSession]:
    """Yield a request-scoped database session.

    This is the single DB seam every route depends on, so tests override it
    with an in-memory SQLite session factory.
    """
    async for session in get_db_session():
        yield session


DbSessionDep = Annotated[AsyncSession, Depends(get_db)]


def get_settings_dep() -> AppSettings:
    """Return the app settings singleton (a dependency for easy override)."""
    return get_app_settings()


AppSettingsDep = Annotated[AppSettings, Depends(get_settings_dep)]


def encode_user_id(user_id: uuid.UUID, settings: AppSettings) -> str:
    """Sign a user id into an opaque, tamper-evident session token."""
    serializer = URLSafeTimedSerializer(settings.cookie_secret, salt="session")
    return serializer.dumps(str(user_id))


def decode_user_id(token: str, settings: AppSettings) -> uuid.UUID | None:
    """Verify and decode a session token, or ``None`` when invalid/expired."""
    serializer = URLSafeTimedSerializer(settings.cookie_secret, salt="session")
    try:
        raw = serializer.loads(token, max_age=settings.session_lifetime_seconds)
    except BadSignature:
        return None
    try:
        return uuid.UUID(raw)
    except (ValueError, TypeError):
        return None


async def get_current_user(
    db: DbSessionDep,
    settings: AppSettingsDep,
    session_token: Annotated[str | None, Cookie(alias="slopolis_session")] = None,
) -> User:
    """Resolve the authenticated user from the signed cookie, or raise 401."""
    if not session_token:
        raise ApiError(401, "unauthorized", "Sign in with GitHub to continue.")
    user_id = decode_user_id(session_token, settings)
    if user_id is None:
        raise ApiError(401, "unauthorized", "Your session has expired; sign in again.")
    user = await db.get(User, user_id)
    if user is None:
        raise ApiError(401, "unauthorized", "Your session is no longer valid.")
    return user


CurrentUserDep = Annotated[User, Depends(get_current_user)]


async def get_optional_user(
    db: DbSessionDep,
    settings: AppSettingsDep,
    session_token: Annotated[str | None, Cookie(alias="slopolis_session")] = None,
) -> User | None:
    """Resolve the signed-in user, or ``None`` when there is no valid session.

    Browser-navigation routes (the GitHub install callback) need to *redirect*
    an anonymous visitor to sign-in rather than answer with a 401 envelope.
    """
    if not session_token:
        return None
    user_id = decode_user_id(session_token, settings)
    if user_id is None:
        return None
    return await db.get(User, user_id)


OptionalUserDep = Annotated[User | None, Depends(get_optional_user)]


async def get_workspace_id(user: CurrentUserDep) -> uuid.UUID:
    """Return the workspace id this user belongs to, or 409 when they have none.

    Every workspace-scoped route depends on this, so a signed-in user who has not
    created or joined a workspace gets one actionable error instead of empty data.
    """
    if user.workspace_id is None:
        raise ApiError(409, "no_workspace", "Create or join a workspace to continue.")
    return user.workspace_id


WorkspaceIdDep = Annotated[uuid.UUID, Depends(get_workspace_id)]


async def get_github_gateway(
    request: Request, db: DbSessionDep, workspace_id: WorkspaceIdDep
) -> GitHubGatewayAdapter:
    """Build the pre-flight GitHub gateway from the app-owned core client."""
    client = _client_from_app(request)
    if client is None:
        raise ApiError(
            503,
            "github_not_configured",
            "The GitHub App is not configured for this workspace.",
        )
    return GitHubGatewayAdapter(client)


GitHubGatewayDep = Annotated[GitHubGatewayAdapter, Depends(get_github_gateway)]


def _client_from_app(request: Request) -> GitHubClient | None:
    """Return the app-owned ``GitHubClient``, or ``None`` when unconfigured."""
    client: GitHubClient | None = getattr(request.app.state, "github_client", None)
    return client


async def get_preflight_service(
    request: Request, db: DbSessionDep, workspace_id: WorkspaceIdDep
) -> PreflightService:
    """Assemble a :class:`PreflightService` from the app's wired adapters."""
    gateway = _gateway_from_app(request)
    workspace = WorkspaceConfigAdapter(db, workspace_id)
    live_check = _live_check_from_app(request)
    return PreflightService(gateway, workspace, live_check)


PreflightServiceDep = Annotated[PreflightService, Depends(get_preflight_service)]


def _gateway_from_app(request: Request) -> GitHubGatewayAdapter:
    """Return the app-owned gateway, or fail with a clear configuration error."""
    gateway: GitHubGatewayAdapter | None = getattr(
        request.app.state, "github_gateway", None
    )
    if gateway is None:
        raise ApiError(
            503,
            "github_not_configured",
            "The GitHub App is not configured for this workspace.",
        )
    return gateway


def _live_check_from_app(request: Request) -> LlmLiveModelCheck:
    """Return the app-owned live model check, building a lazy default if absent."""
    check: LlmLiveModelCheck | None = getattr(
        request.app.state, "live_model_check", None
    )
    if check is not None:
        return check
    llm: LlmClient | None = getattr(request.app.state, "llm_client", None)
    if llm is None:
        raise ApiError(
            503,
            "model_gateway_not_configured",
            "No model gateway is configured for live checks.",
        )
    return LlmLiveModelCheck(llm)


async def get_arq_pool(request: Request) -> ArqPool:
    """Return the app-owned ARQ Redis pool.

    Created once during startup and stored on ``app.state``. Tests override
    this dependency with an in-memory fake, so no Redis is required.
    """
    pool: ArqPool | None = getattr(request.app.state, "arq_pool", None)
    if pool is None:
        raise ApiError(503, "queue_unavailable", "The job queue is not available.")
    return pool


ArqPoolDep = Annotated[ArqPool, Depends(get_arq_pool)]
