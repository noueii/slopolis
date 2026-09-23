"""``preflight.ports.GitHubGateway`` implemented over per-repository clients.

The core :class:`~slopolis_core.github.client.GitHubClient` returns strict
models and raises :class:`~slopolis_core.github.errors.GitHubError` subclasses.
This adapter maps that surface onto the consumer-side port the pre-flight
service expects, translating typed GitHub failures into :class:`ApiError`s so
the HTTP layer can render them in the standard envelope.

A workspace can hold several installations, so the gateway is not handed one
client at construction: every operation resolves the client for the
**repository it touches** through :class:`RepositoryClients` and memoizes it for
the life of the request — one pre-flight run reads each of its repositories
through that repository's own installation. A repository that resolves to no
client (the workspace has no row for it, or its installation cannot mint a
token) fails with the same ``503 github_not_configured`` a workspace with no
App once produced, so pre-flight fails loudly instead of reporting every link as
unresolvable.

``read_repo_file`` reads at the repository's default branch (the port does not
carry a ref); a missing file is a normal ``None``, not an error.

``publish_permissions`` reads what the repository's installation was granted and
memoizes it per installation for the life of the request. It is the one read
whose failure is *not* translated into an :class:`ApiError`: pre-flight reports it
as a notice and refuses that link, so an unanswerable permission read refuses a
submission rather than failing the request.
"""

from __future__ import annotations

import logging
from typing import Protocol

from app.errors import ApiError
from app.services.github_clients import WorkspaceRepository
from slopolis_core.github.client import GitHubClient
from slopolis_core.github.errors import (
    GitHubAuthError,
    GitHubError,
    GitHubNotFoundError,
    GitHubRateLimitError,
)
from slopolis_core.github.limits import PR_URL_RE
from slopolis_core.preflight.models import PrReference, RepositoryRef

__all__ = ["GitHubGatewayAdapter", "RepositoryClients", "as_api_error"]

_logger = logging.getLogger(__name__)


class RepositoryClients(Protocol):
    """Resolves a repository to the client for its own installation.

    ``app.services.github_clients.WorkspaceRepositories`` implements it. The
    protocol lives here because the gateway is its consumer.
    """

    async def client_for(self, full_name: str) -> GitHubClient | None: ...

    async def installation_clients(self) -> list[GitHubClient]: ...

    async def resolve(self, full_name: str) -> WorkspaceRepository | None: ...


class GitHubGatewayAdapter:
    """Read-only GitHub operations for pre-flight, over per-repository clients."""

    def __init__(self, repositories: RepositoryClients) -> None:
        self._repositories = repositories
        self._clients: dict[str, GitHubClient] = {}
        self._default_branches: dict[str, str] = {}
        #: Granted permissions per installation id, for the life of the request:
        #: a submission of several links must not ask the same installation once
        #: per link, and a short-lived answer must not be trusted across requests
        #: — a permission revoked between submits is exactly what this check
        #: exists to catch (spec 10.3).
        self._permissions: dict[int, dict[str, str]] = {}

    async def resolve_pr(self, url: str) -> PrReference:
        """Resolve a PR URL to a typed reference.

        Raises :class:`LookupError` when the URL is malformed or the PR is
        missing — the port contract the pre-flight service discriminates on. The
        repository is parsed out of the URL first, so the read is issued through
        the installation that grants it.
        """
        match = PR_URL_RE.match(url.strip())
        if match is None:
            raise LookupError(f"Not a valid GitHub pull request URL: {url!r}")
        client = await self._client_for(f"{match.group('owner')}/{match.group('repo')}")
        try:
            pull = await client.resolve_pr(url)
        except GitHubNotFoundError as exc:
            raise LookupError(str(exc)) from exc
        except GitHubError as exc:
            raise as_api_error(exc) from exc

        self._default_branches[pull.repo_full_name] = pull.default_branch
        return PrReference(
            url=pull.url,
            repository=RepositoryRef(
                id=_repo_id(pull.repo_full_name),
                full_name=pull.repo_full_name,
                private=pull.private,
                default_branch=pull.default_branch,
            ),
            number=pull.number,
            title=pull.title,
        )

    async def list_covered_repos(self) -> list[str]:
        """Return every repository the workspace's installations can read.

        A repository the workspace has **parked** (spec 10.1) is connected —
        GitHub still grants it — but this workspace has switched it off, so it
        is left out of the coverage set: the pre-flight service then refuses it,
        and the API maps that refusal onto the parked reason rather than the
        generic uncovered one.
        """
        clients = await self._repositories.installation_clients()
        if not clients:
            raise _no_client()
        covered: list[str] = []
        for client in clients:
            try:
                repositories = await client.list_installation_repositories()
            except GitHubError as exc:
                raise as_api_error(exc) from exc
            for repo in repositories:
                self._default_branches[repo.full_name] = repo.default_branch
                if await self._is_parked(repo.full_name):
                    continue
                covered.append(repo.full_name)
        return covered

    async def _is_parked(self, full_name: str) -> bool:
        """Whether the workspace has switched ``full_name`` off.

        A repository the workspace holds no row for is not parked: nothing was
        ever switched off about it, and session creation records its row on
        demand.
        """
        resolved = await self._repositories.resolve(full_name)
        return resolved is not None and not resolved.row.enabled

    async def user_has_access(
        self,
        repo_full_name: str,
        *,
        private: bool,
        user_login: str,
    ) -> bool:
        """Return whether ``user_login`` may trigger a review on the repo.

        The bar is the spec rule, applied by the repository's own client from
        the ``private`` flag pre-flight passes down.
        """
        client = await self._client_for(repo_full_name)
        return await client.user_can_trigger(
            repo_full_name,
            private=private,
            user_login=user_login,
        )

    async def read_repo_file(self, repo_full_name: str, path: str) -> str | None:
        """Read a file at the repo's default branch, or ``None`` when absent."""
        client = await self._client_for(repo_full_name)
        ref = self._default_branches.get(repo_full_name, "main")
        try:
            return await client.read_file(repo_full_name, path, ref)
        except GitHubNotFoundError:
            return None
        except GitHubError as exc:
            raise as_api_error(exc) from exc

    async def publish_permissions(self, repo_full_name: str) -> dict[str, str] | None:
        """What the repository's installation was granted, or ``None``.

        Read through the client that already serves ``repo_full_name``; the
        answer is memoized per installation id for the life of the request, so a
        submission carrying several links on one installation reads it once. A
        failure to read is ``None``, not an :class:`ApiError`: pre-flight renders
        it as a notice and refuses that link, because a check that cannot run
        must not pass a submission whose whole point is that publishing will
        succeed (spec 10.3 §Publish prerequisites). Whether what it *does* read
        refuses the link or only notices a skipped check run is core's
        required/optional split — this read does not judge a scope.
        """
        client = await self._client_for(repo_full_name)
        installation_id = client.installation_id
        if installation_id is not None:
            cached = self._permissions.get(installation_id)
            if cached is not None:
                return cached
        try:
            permissions = await client.installation_permissions()
        except GitHubError as exc:
            # The failure is the caller's notice, so it must not raise; the
            # warning keeps it visible to operators, the way a degraded
            # installation is already reported elsewhere (spec 10.1).
            _logger.warning(
                "installation permissions unreadable for %s: %s", repo_full_name, exc
            )
            return None
        if installation_id is not None:
            self._permissions[installation_id] = permissions
        return permissions

    async def _client_for(self, full_name: str) -> GitHubClient:
        """The client for ``full_name``'s installation, memoized for this request."""
        cached = self._clients.get(full_name)
        if cached is not None:
            return cached
        client = await self._repositories.client_for(full_name)
        if client is None:
            raise _no_client()
        self._clients[full_name] = client
        return client


def _no_client() -> ApiError:
    """The failure a workspace with no readable GitHub client produced before."""
    return ApiError(
        503,
        "github_not_configured",
        "The GitHub App is not configured for this workspace.",
    )


def _repo_id(full_name: str) -> str:
    """Derive a stable opaque id for a repository reference."""
    return f"repo_{full_name.lower().replace('/', '_')}"


def as_api_error(exc: GitHubError) -> ApiError:
    """Translate a typed GitHub failure into the matching API error."""
    if isinstance(exc, GitHubNotFoundError):
        return ApiError(
            404,
            "github_not_found",
            "The GitHub resource was not found.",
            detail=str(exc),
        )
    if isinstance(exc, GitHubRateLimitError):
        return ApiError(
            429,
            "github_rate_limited",
            "GitHub rate limit reached. Try again shortly.",
            detail=str(exc),
        )
    if isinstance(exc, GitHubAuthError):
        return ApiError(
            502,
            "github_auth_failed",
            "The GitHub App installation could not authenticate.",
            detail=str(exc),
        )
    return ApiError(502, "github_unavailable", "GitHub is unavailable.", detail=str(exc))
