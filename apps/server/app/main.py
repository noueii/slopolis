"""FastAPI application entrypoint for the Slopolis API server.

Builds the app, mounts every router under ``/api``, wires CORS for the dev SPA,
installs the standard error envelope, and — when the environment provides them —
opens the GitHub App surfaces, the OAuth client, and the ARQ pool once for the
process. All startup wiring is best-effort: a self-hosted server with no GitHub
or Redis configured still boots and serves ``/healthz`` and ``/openapi.json``.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.auth import GitHubOAuthClient, me_router
from app.auth import router as auth_router
from app.config import AppSettings, get_app_settings
from app.errors import install_error_handlers
from app.routers import (
    catalog,
    dashboard,
    events,
    github_install,
    providers,
    repositories,
    reviews,
    runs,
    sessions,
    usage,
    webhooks,
    workspaces,
)

__all__ = ["app", "create_app"]

_API_PREFIX = "/api"

_logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Open process-wide clients; close them on shutdown."""
    settings = get_app_settings()
    await _open_github(app, settings)
    _open_llm(app)
    oauth = GitHubOAuthClient()
    app.state.oauth_client = oauth
    await _open_arq(app, settings)
    try:
        yield
    finally:
        await _close_arq(app)
        await _close_llm(app)
        await oauth.aclose()


def _open_llm(app: FastAPI) -> None:
    """Open the model gateway client the live check and any model call needs.

    Also opens the pool the live check keeps its **per-credential** clients in
    (spec 10.2): a workspace whose model is served by its own vault credential is
    called through a client of its own, and those clients outlive a request, so
    the pool is opened once here and closed on shutdown.

    Without a gateway the server has no way to reach a provider at all, and every
    submission used to answer 503 from a dependency before pre-flight could say
    what was actually wrong. A deployment that has not configured a gateway
    still boots — reads, the install flow and settings all work — and pre-flight
    reports the missing gateway as a validation notice naming what to set.
    """
    from app.services.live_check import CredentialClientPool
    from slopolis_core.llm.client import LiteLlmClient, LlmAuthError

    app.state.live_check_clients = CredentialClientPool()
    try:
        app.state.llm_client = LiteLlmClient.from_settings()
    except LlmAuthError as exc:
        # One line at a level an operator sees, because "reviews cannot run" is
        # the consequence and the cause is one environment variable.
        _logger.warning("Model gateway is not configured: %s", exc)
        app.state.llm_client = None


async def _close_llm(app: FastAPI) -> None:
    """Close the gateway connection pool and every pooled credential client."""
    llm = getattr(app.state, "llm_client", None)
    close = getattr(llm, "aclose", None)
    if close is not None:
        await close()
    clients = getattr(app.state, "live_check_clients", None)
    if clients is not None:
        await clients.aclose()


async def _open_github(app: FastAPI, settings: AppSettings) -> None:
    """Wire the App-JWT surface and the per-installation client registry."""
    core = settings.core
    if not core.github_app_id or not core.github_app_private_key:
        app.state.github_clients = None
        app.state.app_installations = None
        return

    from app.services.github_clients import InstallationClients
    from slopolis_core.github.app_installations import AppInstallations

    app_id = int(core.github_app_id)
    private_key = core.github_app_private_key
    # The App-JWT surface needs no network at construction, so the install flow
    # works before any installation is recorded. Neither half resolves an
    # installation here: the App may have none yet, a workspace may hold several,
    # and gaining one must not need a restart — the registry mints a token for
    # whichever installation a request reads through, and caches it until it
    # nears expiry.
    app.state.app_installations = AppInstallations(app_id, private_key)
    app.state.github_clients = InstallationClients(app_id, private_key)


async def _open_arq(app: FastAPI, settings: AppSettings) -> None:
    """Create the ARQ Redis pool, tolerating an unavailable Redis at boot."""
    app.state.arq_pool = None
    try:
        from arq import create_pool
        from arq.connections import RedisSettings

        app.state.arq_pool = await create_pool(RedisSettings.from_dsn(settings.core.redis_url))
    except Exception:
        app.state.arq_pool = None


async def _close_arq(app: FastAPI) -> None:
    """Close the ARQ pool if one was opened."""
    pool = getattr(app.state, "arq_pool", None)
    if pool is None:
        return
    close = getattr(pool, "aclose", None)
    if close is not None:
        await close()


def create_app() -> FastAPI:
    """Build and configure the FastAPI application."""
    settings = get_app_settings()
    application = FastAPI(title="Slopolis API", version="0.0.1", lifespan=_lifespan)

    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    install_error_handlers(application)

    for router in _routers():
        application.include_router(router, prefix=_API_PREFIX)

    @application.get("/healthz")
    async def healthz() -> dict[str, str]:
        """Liveness probe."""
        return {"status": "ok"}

    return application


def _routers() -> list[APIRouter]:
    """Return every router mounted under ``/api``."""
    return [
        auth_router,
        me_router,
        workspaces.router,
        github_install.router,
        catalog.router,
        providers.router,
        repositories.router,
        dashboard.router,
        reviews.router,
        sessions.router,
        events.router,
        runs.router,
        usage.router,
        webhooks.router,
    ]


app = create_app()
