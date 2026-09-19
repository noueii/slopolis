"""``preflight.ports.GitHubGateway`` implemented over the core GitHub client.

The core :class:`~slopolis_core.github.client.GitHubClient` returns strict
models and raises :class:`~slopolis_core.github.errors.GitHubError` subclasses.
This adapter maps that surface onto the consumer-side port the pre-flight
service expects, translating typed GitHub failures into :class:`ApiError`s so
the HTTP layer can render them in the standard envelope.

``read_repo_file`` reads at the repository's default branch (the port does not
carry a ref); a missing file is a normal ``None``, not an error.
"""

from __future__ import annotations

from app.errors import ApiError
from slopolis_core.github.client import GitHubClient
from slopolis_core.github.errors import (
    GitHubAuthError,
    GitHubError,
    GitHubNotFoundError,
    GitHubRateLimitError,
)
from slopolis_core.preflight.models import PrReference, RepositoryRef

__all__ = ["GitHubGatewayAdapter"]


class GitHubGatewayAdapter:
    """Read-only GitHub operations for pre-flight, over a core client."""

    def __init__(self, client: GitHubClient) -> None:
        self._client = client
        self._default_branches: dict[str, str] = {}

    async def resolve_pr(self, url: str) -> PrReference:
        """Resolve a PR URL to a typed reference.

        Raises :class:`LookupError` when the URL is malformed or the PR is
        missing — the port contract the pre-flight service discriminates on.
        """
        try:
            pull = await self._client.resolve_pr(url)
        except GitHubNotFoundError as exc:
            raise LookupError(str(exc)) from exc
        except GitHubError as exc:
            raise _as_api_error(exc) from exc

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
        """Return every repository the installation can read."""
        try:
            repositories = await self._client.list_installation_repositories()
        except GitHubError as exc:
            raise _as_api_error(exc) from exc
        for repo in repositories:
            self._default_branches[repo.full_name] = repo.default_branch
        return [repo.full_name for repo in repositories]

    async def user_has_access(
        self, repo_full_name: str, *, private: bool, user_login: str
    ) -> bool:
        """Return whether ``user_login`` may trigger a review on the repo."""
        return await self._client.user_can_trigger(
            repo_full_name, private=private, user_login=user_login
        )

    async def read_repo_file(self, repo_full_name: str, path: str) -> str | None:
        """Read a file at the repo's default branch, or ``None`` when absent."""
        ref = self._default_branches.get(repo_full_name, "main")
        try:
            return await self._client.read_file(repo_full_name, path, ref)
        except GitHubNotFoundError:
            return None
        except GitHubError as exc:
            raise _as_api_error(exc) from exc


def _repo_id(full_name: str) -> str:
    """Derive a stable opaque id for a repository reference."""
    return f"repo_{full_name.lower().replace('/', '_')}"


def _as_api_error(exc: GitHubError) -> ApiError:
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
