"""FastAPI dependencies: DB session, current user, GitHub clients, pre-flight.

Every dependency is an ordinary callable so tests can swap it with
``app.dependency_overrides`` without touching Redis, GitHub, or a real
database. In particular ``get_arq_pool`` and ``get_preflight_service`` are the
two seams the test-suite overrides.

GitHub is installation-aware: ``get_github_clients`` hands out the process-wide
registry of per-installation clients, and ``get_workspace_repositories`` turns
this workspace's repository rows into the client for each row's **own**
installation, so a workspace with several installations reads all of them and a
new installation needs no restart.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from functools import lru_cache
from typing import Annotated, Protocol

from fastapi import Cookie, Depends, Request
from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.github import GitHubGatewayAdapter
from app.adapters.workspace import WorkspaceConfigAdapter
from app.config import AppSettings, get_app_settings
from app.errors import ApiError
from app.services.github_clients import (
    InstallationClientSource,
    WorkspaceRepositories,
)
from app.services.repo_access import GitHubRepoProbe, RepoAccessChecker
from slopolis_core.llm.client import LlmClient
from slopolis_core.preflight.ports import LlmLiveModelCheck
from slopolis_core.preflight.service import PreflightService
from slopolis_core.vault import SecretVault, VaultDecryptError, VaultNotConfigured
from slopolis_db.models import User
from slopolis_db.session import get_db_session

__all__ = [
    "AdminUserDep",
    "AppSettingsDep",
    "ArqPool",
    "ArqPoolDep",
    "CurrentUserDep",
    "DbSessionDep",
    "GitHubClientsDep",
    "GitHubGatewayDep",
    "OptionalUserDep",
    "OptionalVaultDep",
    "PreflightServiceDep",
    "RepoAccessCheckerDep",
    "VaultDep",
    "WorkspaceIdDep",
    "WorkspaceRepositoriesDep",
    "decode_user_id",
    "encode_user_id",
    "get_admin_user",
    "get_arq_pool",
    "get_current_user",
    "get_db",
    "get_github_clients",
    "get_github_gateway",
    "get_optional_user",
    "get_optional_vault",
    "get_preflight_service",
    "get_repo_access_checker",
    "get_settings_dep",
    "get_vault",
    "get_workspace_id",
    "get_workspace_repositories",
    "user_github_token",
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


async def get_github_clients(request: Request) -> InstallationClientSource | None:
    """Return the app's per-installation client registry, or ``None`` if unconfigured.

    ``None`` is the unconfigured-App path: routes that still have something to
    show without GitHub (the repository list) degrade, and the ones that need a
    read report the missing client as a 503.
    """
    source: InstallationClientSource | None = getattr(
        request.app.state, "github_clients", None
    )
    return source


GitHubClientsDep = Annotated[
    InstallationClientSource | None, Depends(get_github_clients)
]


async def get_workspace_repositories(
    db: DbSessionDep,
    workspace_id: WorkspaceIdDep,
    clients: GitHubClientsDep,
) -> WorkspaceRepositories:
    """Resolve this workspace's repositories to clients for their installations."""
    return WorkspaceRepositories(db, workspace_id, clients)


WorkspaceRepositoriesDep = Annotated[
    WorkspaceRepositories, Depends(get_workspace_repositories)
]


async def get_github_gateway(
    repositories: WorkspaceRepositoriesDep,
) -> GitHubGatewayAdapter:
    """Wrap this workspace's per-repository resolution in the pre-flight gateway."""
    return GitHubGatewayAdapter(repositories)


GitHubGatewayDep = Annotated[GitHubGatewayAdapter, Depends(get_github_gateway)]


async def get_preflight_service(
    request: Request,
    db: DbSessionDep,
    workspace_id: WorkspaceIdDep,
    repositories: WorkspaceRepositoriesDep,
) -> PreflightService:
    """Assemble a :class:`PreflightService` from the app's wired adapters."""
    gateway = GitHubGatewayAdapter(repositories)
    workspace = WorkspaceConfigAdapter(db, workspace_id)
    live_check = _live_check_from_app(request)
    return PreflightService(gateway, workspace, live_check)


PreflightServiceDep = Annotated[PreflightService, Depends(get_preflight_service)]


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


@lru_cache
def get_vault() -> SecretVault:
    """Return the process-wide secret vault, or 503 when it is unconfigured.

    Decoding and stretching the master key is pure work, so the parsed vault is
    cached; tests that change ``ENCRYPTION_KEY`` clear it with
    ``get_vault.cache_clear()``. An unset or too-short key is a configuration
    error surfaced as a typed 503 rather than a weakened key.
    """
    try:
        return SecretVault.from_settings()
    except VaultNotConfigured as exc:
        raise ApiError(
            503,
            "vault_not_configured",
            "Set ENCRYPTION_KEY to at least 32 bytes of material before storing credentials.",
        ) from exc


VaultDep = Annotated[SecretVault, Depends(get_vault)]


def get_optional_vault() -> SecretVault | None:
    """Return the vault, or ``None`` when ``ENCRYPTION_KEY`` is unconfigured.

    Reads must not fail the way *storing* a credential rightly does: a deployment
    with no vault still signs users in and serves sessions, it just cannot answer
    repo-access checks (spec 10.1, 10.8 §Access). Hence this is not ``VaultDep``.
    """
    try:
        return get_vault()
    except ApiError:
        return None


OptionalVaultDep = Annotated[SecretVault | None, Depends(get_optional_vault)]


def user_github_token(user: User) -> str | None:
    """Open a user's stored GitHub token, or ``None`` when it cannot be used.

    No stored token (signed in without a vault) and a blob the current master key
    cannot open (``ENCRYPTION_KEY`` rotated) both mean *unverifiable*, never a
    plaintext fallback and never a log line.
    """
    blob = user.encrypted_github_token
    if blob is None:
        return None
    vault = get_optional_vault()
    if vault is None:
        return None
    try:
        return vault.open(blob)
    except VaultDecryptError:
        return None


@lru_cache
def _repo_access_checker() -> RepoAccessChecker:
    """Build the process-wide checker, so its TTL cache spans requests.

    The vault is resolved per check (through :func:`user_github_token`), not
    captured here, so clearing the vault memo after an ``ENCRYPTION_KEY`` change
    is enough to make the checker see the new key.
    """
    return RepoAccessChecker(probe=GitHubRepoProbe(), tokens=user_github_token)


async def get_repo_access_checker() -> RepoAccessChecker:
    """Return the per-viewer repository access checker (spec 10.8 §Access)."""
    return _repo_access_checker()


RepoAccessCheckerDep = Annotated[RepoAccessChecker, Depends(get_repo_access_checker)]


async def get_admin_user(user: CurrentUserDep) -> User:
    """Require a workspace admin: provider and catalog configuration is admin-only."""
    if not user.is_admin:
        raise ApiError(
            403,
            "admin_required",
            "Only workspace admins can change provider configuration.",
        )
    return user


AdminUserDep = Annotated[User, Depends(get_admin_user)]
