"""App-JWT operations behind the GitHub App install flow (spec 01).

The install flow runs before any installation row exists, so these calls are
signed with the App's own RS256 JWT rather than an installation token. The App's
slug identifies it to the OAuth/install URLs; one installation resolves to the
account it's installed on; and that installation's repositories are read with a
short-lived installation token minted on demand.

Every failure surfaces as a :class:`slopolis_core.github.errors.GitHubError`, and
no request is made at construction time.
"""

from __future__ import annotations

import githubkit.auth
from githubkit import GitHub, TokenAuthStrategy

from slopolis_core.github._mapping import raise_for_status
from slopolis_core.github.errors import GitHubError
from slopolis_core.github.models import AppInstallation, InstallationRepository
from slopolis_core.github.transport import translate_errors

__all__ = ["MAX_REPO_PAGES", "PER_PAGE", "AppInstallations"]

#: Page size for paginated reads, mirroring :class:`~slopolis_core.github.client.GitHubClient`.
PER_PAGE = 100
#: Hard cap on pages fetched while listing one installation's repositories.
MAX_REPO_PAGES = 10

AppClient = GitHub[githubkit.auth.AppAuthStrategy]


class AppInstallations:
    """App-JWT operations: the App's own metadata, its installations, and their repositories."""

    def __init__(self, app_id: int, private_key: str) -> None:
        self._app_id = app_id
        self._app = GitHub(
            githubkit.auth.AppAuthStrategy(app_id, private_key),
            rest_api_validate_body=False,
        )

    @translate_errors
    async def app_slug(self) -> str:
        """Return the App's slug, used to build its install and OAuth URLs."""
        response = await self._app.rest.apps.async_get_authenticated()
        raise_for_status(response, "the authenticated app")
        app = response.parsed_data
        slug = app.slug if app is not None else None
        if not isinstance(slug, str) or not slug:
            raise GitHubError(f"GitHub App {self._app_id} has no slug")
        return slug

    @translate_errors
    async def get_installation(self, installation_id: int) -> AppInstallation:
        """Fetch one installation, or raise :class:`GitHubNotFoundError`."""
        response = await self._app.rest.apps.async_get_installation(installation_id)
        raise_for_status(response, f"installation {installation_id}")
        item = response.parsed_data
        account = item.account
        return AppInstallation(
            installation_id=item.id,
            account_login=_account_login(account),
            account_type=_text(item.target_type)
            or _text(getattr(account, "type", None)),
            repository_selection=_text(item.repository_selection, default="selected"),
            suspended=item.suspended_at is not None,
        )

    @translate_errors
    async def list_repositories(self, installation_id: int) -> list[InstallationRepository]:
        """List the repositories an installation can access, newest pages first.

        Mints an installation access token, then pages
        ``GET /installation/repositories`` up to :data:`MAX_REPO_PAGES` pages of
        :data:`PER_PAGE` entries.
        """
        token_response = await self._app.rest.apps.async_create_installation_access_token(
            installation_id
        )
        raise_for_status(token_response, f"installation token for {installation_id}")
        installation = GitHub(TokenAuthStrategy(token_response.parsed_data.token))
        repositories: list[InstallationRepository] = []
        for page in range(1, MAX_REPO_PAGES + 1):
            response = await installation.rest.apps.async_list_repos_accessible_to_installation(
                per_page=PER_PAGE, page=page
            )
            raise_for_status(response, f"repositories for installation {installation_id}")
            items = response.parsed_data.repositories
            repositories.extend(
                InstallationRepository(
                    github_id=repo.id,
                    full_name=repo.full_name,
                    private=repo.private,
                    default_branch=_text(repo.default_branch, default="main"),
                )
                for repo in items
            )
            if len(items) < PER_PAGE:
                break
        return repositories


def _text(value: object, *, default: str = "") -> str:
    """Return ``value`` when it is a non-empty string, else ``default``.

    githubkit leaves absent optional fields as its ``Unset`` sentinel (an enum,
    not a ``str``), so ``None`` checks alone would let it through.
    """
    return value if isinstance(value, str) and value else default


def _account_login(account: object) -> str:
    """Resolve an installation account's login.

    Organization and user accounts carry ``login``; an enterprise account only
    carries ``name`` and ``slug``. A ``None`` account yields an empty login.
    """
    for field in ("login", "name", "slug"):
        login = _text(getattr(account, field, None))
        if login:
            return login
    return ""
