"""GitHub App install and setup callback (spec 10.1).

Two browser-navigation routes, deliberately not JSON endpoints:

- ``GET /api/github/install`` sends the user to GitHub's install page.
- ``GET /api/github/setup`` is the App's **Setup URL**. GitHub redirects the
  browser here after an install (and after a repository-selection change, when
  "Redirect on update" is on), carrying ``installation_id`` and ``setup_action``.
  This is the only place the server learns an installation id.

Because GitHub drives these with redirects, a missing session or a missing
workspace sends the browser back into the app to sign in or onboard — only
genuine configuration failures raise the JSON error envelope.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Request, status
from fastapi.responses import RedirectResponse

from app.adapters.github import as_api_error
from app.config import AppSettings
from app.deps import AppSettingsDep, DbSessionDep, OptionalUserDep
from app.errors import ApiError
from app.services.installation_sync import (
    InstallationClaimedError,
    InstallationSource,
    sync_installation,
)
from slopolis_core.github.errors import GitHubError

__all__ = ["router"]

router = APIRouter(tags=["github"])

#: Where GitHub serves the install page for a public App.
_INSTALL_URL = "https://github.com/apps/{slug}/installations/new"
#: Where the browser goes when it has no session: the login route is a redirect too.
_LOGIN_PATH = "/api/auth/github/login"
#: `setup_action` value GitHub sends when a user only *requests* an installation.
_REQUEST_ACTION = "request"


@router.get("/github/install")
async def install(
    request: Request,
    settings: AppSettingsDep,
    _user: OptionalUserDep,
) -> RedirectResponse:
    """Send the browser to GitHub's install page for this App."""
    slug = await _resolve_slug(request, settings)
    return RedirectResponse(_INSTALL_URL.format(slug=slug), status_code=status.HTTP_302_FOUND)


@router.get("/github/setup")
async def setup(
    request: Request,
    db: DbSessionDep,
    settings: AppSettingsDep,
    user: OptionalUserDep,
    installation_id: Annotated[int | None, Query()] = None,
    setup_action: Annotated[str | None, Query()] = None,
) -> RedirectResponse:
    """Record the installation GitHub just created, then return to the app."""
    app_url = settings.core.app_url
    if user is None:
        return RedirectResponse(_LOGIN_PATH, status_code=status.HTTP_302_FOUND)
    if (
        installation_id is None
        or user.workspace_id is None
        or setup_action == _REQUEST_ACTION
    ):
        # Nothing to record yet: an approval request, or an account that has to
        # onboard first. The app decides what to show.
        return RedirectResponse(app_url, status_code=status.HTTP_302_FOUND)

    try:
        await sync_installation(
            db,
            workspace_id=user.workspace_id,
            installation_id=installation_id,
            source=_source_from_app(request),
        )
    except InstallationClaimedError as exc:
        raise ApiError(
            status.HTTP_409_CONFLICT,
            "installation_claimed",
            "That GitHub App installation already belongs to another workspace.",
            detail=str(exc),
        ) from exc
    except GitHubError as exc:
        raise as_api_error(exc) from exc

    return RedirectResponse(app_url, status_code=status.HTTP_302_FOUND)


async def _resolve_slug(request: Request, settings: AppSettings) -> str:
    """The App's slug for the install URL: configured, cached, or read from GitHub."""
    configured = settings.core.github_app_slug
    if configured:
        return configured
    cached = getattr(request.app.state, "github_app_slug", None)
    if isinstance(cached, str) and cached:
        return cached
    try:
        slug = await _source_from_app(request).app_slug()
    except GitHubError as exc:
        raise as_api_error(exc) from exc
    if not slug:
        raise ApiError(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "github_app_slug_missing",
            "The GitHub App's slug is unknown, so the install page cannot be built.",
            detail="Set GITHUB_APP_SLUG, or check that the App credentials are valid.",
        )
    request.app.state.github_app_slug = slug
    return slug


def _source_from_app(request: Request) -> InstallationSource:
    """Return the App-JWT installation source, or fail with a config error."""
    source: InstallationSource | None = getattr(
        request.app.state, "app_installations", None
    )
    if source is None:
        raise ApiError(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "github_not_configured",
            "The GitHub App is not configured for this workspace.",
        )
    return source

