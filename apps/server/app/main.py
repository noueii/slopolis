"""FastAPI application entrypoint for the Slopolis API server.

Builds the app, mounts every router under ``/api``, wires CORS for the dev SPA,
installs the standard error envelope, and — when the environment provides them —
opens the GitHub client, OAuth client, and ARQ pool once for the process. All
startup wiring is best-effort: a self-hosted server with no GitHub or Redis
configured still boots and serves ``/healthz`` and ``/openapi.json``.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.adapters.github import GitHubGatewayAdapter
from app.auth import GitHubOAuthClient, me_router
from app.auth import router as auth_router
from app.config import AppSettings, get_app_settings
from app.errors import install_error_handlers
from app.routers import (
    catalog,
    dashboard,
    events,
    repositories,
    reviews,
    sessions,
    usage,
)

__all__ = ["app", "create_app"]

_API_PREFIX = "/api"


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Open process-wide clients; close them on shutdown."""
    settings = get_app_settings()
    await _open_github(app, settings)
    oauth = GitHubOAuthClient()
    app.state.oauth_client = oauth
    await _open_arq(app, settings)
    try:
        yield
    finally:
        await _close_arq(app)
        await oauth.aclose()


async def _open_github(app: FastAPI, settings: AppSettings) -> None:
    """Create the GitHub client + pre-flight gateway when the App is configured."""
    core = settings.core
    if not core.github_app_id or not core.github_app_private_key:
        app.state.github_client = None
        app.state.github_gateway = None
        return
    from slopolis_core.github.client import GitHubClient

    try:
        client = await GitHubClient.from_app(
            int(core.github_app_id), core.github_app_private_key
        )
    except Exception:
        app.state.github_client = None
        app.state.github_gateway = None
        return
    app.state.github_client = client
    app.state.github_gateway = GitHubGatewayAdapter(client)


async def _open_arq(app: FastAPI, settings: AppSettings) -> None:
    """Create the ARQ Redis pool, tolerating an unavailable Redis at boot."""
    app.state.arq_pool = None
    try:
        from arq import create_pool
        from arq.connections import RedisSettings

        app.state.arq_pool = await create_pool(
            RedisSettings.from_dsn(settings.core.redis_url)
        )
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
        catalog.router,
        repositories.router,
        dashboard.router,
        reviews.router,
        sessions.router,
        events.router,
        usage.router,
    ]


app = create_app()
