"""GitHub REST client for the review harness (spec 10.6).

Bounded, cached, and typed: every method returns a strict model from
:mod:`slopolis_core.github.models`, every failure is a
:class:`slopolis_core.github.errors.GitHubError`, and repeated reads within one
job hit a per-instance cache. Hard caps (:data:`MAX_FILE_BYTES`,
:data:`MAX_DIFF_LINES`, :data:`MAX_FILES`) plus a :class:`ToolBudget` keep the
reviewer from running away.

Context strategy is API-only — no clone (spec 10.6).
"""

from __future__ import annotations

import githubkit.auth
from githubkit import GitHub, TokenAuthStrategy

from slopolis_core.context import PrContext
from slopolis_core.github._mapping import (
    iso,
    parse_expiry,
    raise_for_status,
    split_repo,
    truncate_lines,
)
from slopolis_core.github.auth import InstallationAuth, TokenCache
from slopolis_core.github.errors import GitHubAuthError, GitHubNotFoundError
from slopolis_core.github.limits import (
    CHECK_RUNS_PER_PAGE,
    MAX_DIFF_LINES,
    MAX_FILE_BYTES,
    MAX_FILES,
    MAX_TOOL_CALLS,
    PR_URL_RE,
    ReadCache,
    ToolBudget,
)
from slopolis_core.github.models import (
    ChangedFile,
    CheckRun,
    GitHubIssue,
    GitHubPullRequest,
    GitHubRepository,
)
from slopolis_core.github.repo_reads import RepoReads
from slopolis_core.github.transport import translate_errors

__all__ = [
    "MAX_DIFF_LINES",
    "MAX_FILES",
    "MAX_FILE_BYTES",
    "MAX_TOOL_CALLS",
    "GitHubClient",
    "ToolBudget",
]

AppClient = GitHub[githubkit.auth.AppAuthStrategy]


class GitHubClient:
    """Async GitHub REST client bounded to the reviewer's read surface."""

    def __init__(
        self,
        github: GitHub[TokenAuthStrategy],
        *,
        auth: InstallationAuth,
        app_client: AppClient | None = None,
    ) -> None:
        self._github = github
        self._auth = auth
        self._app_client = app_client
        self._cache = ReadCache()
        self.budget = ToolBudget()
        self._repo_reads = RepoReads(github, cache=self._cache, budget=self.budget)

    # -- constructors -------------------------------------------------------

    @classmethod
    def from_installation_token(cls, token: str) -> GitHubClient:
        """Build a client from an installation access token."""
        auth = InstallationAuth.from_installation_token(token)
        return cls(GitHub(TokenAuthStrategy(token)), auth=auth)

    @classmethod
    @translate_errors
    async def from_app(
        cls,
        app_id: int,
        private_key: str,
        *,
        installation_id: int | None = None,
        cache: TokenCache | None = None,
    ) -> GitHubClient:
        """Mint an installation token via App auth and build a client."""
        app_client = GitHub(
            githubkit.auth.AppAuthStrategy(app_id, private_key),
            rest_api_validate_body=False,
        )
        if installation_id is None:
            installations = await app_client.rest.apps.async_list_installations()
            raise_for_status(installations, "list installations")
            items = installations.parsed_data
            if not items:
                raise GitHubAuthError(
                    f"App {app_id} has no installations; install the app on a repository first"
                )
            installation_id = items[0].id

        token_cache = cache if cache is not None else TokenCache()
        cached = token_cache.get(installation_id)
        if cached is not None:
            auth = InstallationAuth(installation_id=installation_id, token=cached)
            return cls(GitHub(TokenAuthStrategy(cached)), auth=auth, app_client=app_client)

        token_response = await app_client.rest.apps.async_create_installation_access_token(
            installation_id
        )
        raise_for_status(token_response, "create installation token")
        minted = token_response.parsed_data
        token_cache.store(installation_id, minted.token, parse_expiry(minted.expires_at))
        auth = InstallationAuth(installation_id=installation_id, token=minted.token)
        return cls(GitHub(TokenAuthStrategy(minted.token)), auth=auth, app_client=app_client)

    @property
    def installation_id(self) -> int | None:
        """The installation this client reads through, when it has one."""
        return self._auth.installation_id

    # -- PR resolution / reads ---------------------------------------------

    @translate_errors
    async def resolve_pr(self, url: str) -> GitHubPullRequest:
        """Resolve a pull-request URL to a typed pull request.

        Raises :class:`GitHubNotFoundError` for a malformed URL or a missing PR.
        """
        match = PR_URL_RE.match(url.strip())
        if match is None:
            raise GitHubNotFoundError(f"Not a valid GitHub pull request URL: {url!r}")
        number = int(match.group("number"))
        return await self.get_pull_request(f"{match.group('owner')}/{match.group('repo')}", number)

    @translate_errors
    async def get_pull_request(self, repo_full_name: str, number: int) -> GitHubPullRequest:
        """Fetch one pull request, or raise :class:`GitHubNotFoundError`."""
        cached = self._cache.pulls.get((repo_full_name, number))
        if cached is not None:
            return cached
        owner, repo = split_repo(repo_full_name)
        self.budget.consume(method="get_pull_request")
        response = await self._github.rest.pulls.async_get(owner, repo, number)
        raise_for_status(response, f"pull request {repo_full_name}#{number}")
        repository = await self._repo_reads.fetch_repo_meta(repo_full_name)
        pull = response.parsed_data
        result = GitHubPullRequest(
            repo_full_name=repo_full_name,
            private=repository.private,
            number=pull.number,
            title=pull.title,
            url=pull.html_url,
            head_branch=pull.head.ref,
            base_branch=pull.base.ref,
            head_sha=pull.head.sha,
            default_branch=repository.default_branch,
            body=pull.body or "",
            author_login=pull.user.login,
            draft=pull.draft or False,
            changed_files=pull.changed_files or 0,
            additions=pull.additions or 0,
            deletions=pull.deletions or 0,
            updated_at=iso(pull.updated_at),
        )
        self._cache.pulls[(repo_full_name, number)] = result
        return result

    @translate_errors
    async def get_pr_context(
        self,
        repo_full_name: str,
        number: int,
        *,
        max_files: int = MAX_FILES,
        max_diff_lines: int = MAX_DIFF_LINES,
    ) -> PrContext:
        """Build the typed prompt context, truncated to the supplied caps."""
        pull = await self.get_pull_request(repo_full_name, number)
        changed = await self.list_changed_files(repo_full_name, number)
        diff = await self._repo_reads.fetch_diff(repo_full_name, number)
        truncated_diff, _ = truncate_lines(diff, max_diff_lines)
        return PrContext(
            repo_full_name=repo_full_name,
            number=number,
            title=pull.title,
            body=pull.body,
            changed_files=[entry.path for entry in changed[:max_files]],
            diff=truncated_diff,
        )

    @translate_errors
    async def list_check_runs(self, repo_full_name: str, ref: str) -> list[CheckRun]:
        """List the check runs attached to ``ref``, a pull request's head commit."""
        cached = self._cache.checks.get((repo_full_name, ref))
        if cached is not None:
            return cached
        owner, repo = split_repo(repo_full_name)
        self.budget.consume(method="list_check_runs")
        response = await self._github.rest.checks.async_list_for_ref(
            owner, repo, ref, per_page=CHECK_RUNS_PER_PAGE
        )
        raise_for_status(response, f"check runs for {repo_full_name}@{ref}")
        runs = [
            CheckRun(name=run.name, status=run.status, conclusion=run.conclusion)
            for run in response.parsed_data.check_runs
        ]
        self._cache.checks[(repo_full_name, ref)] = runs
        return runs

    @translate_errors
    async def list_changed_files(self, repo_full_name: str, number: int) -> list[ChangedFile]:
        """List every changed file for a pull request."""
        cached = self._cache.files.get((repo_full_name, number))
        if cached is not None:
            return cached
        owner, repo = split_repo(repo_full_name)
        self.budget.consume(method="list_changed_files")
        response = await self._github.rest.pulls.async_list_files(owner, repo, number, per_page=100)
        raise_for_status(response, f"changed files for {repo_full_name}#{number}")
        entries = [
            ChangedFile(
                path=item.filename,
                additions=item.additions,
                deletions=item.deletions,
                status=item.status,
            )
            for item in response.parsed_data
        ]
        self._cache.files[(repo_full_name, number)] = entries
        return entries

    @translate_errors
    async def list_changed_paths(self, repo_full_name: str, number: int) -> list[str]:
        """Return just the changed file paths for a pull request."""
        return [entry.path for entry in await self.list_changed_files(repo_full_name, number)]

    @translate_errors
    async def read_file(self, repo_full_name: str, path: str, ref: str) -> str:
        """Fetch and decode a file at ``ref``, truncating past the byte cap."""
        return await self._repo_reads.read_file(repo_full_name, path, ref)

    @translate_errors
    async def list_dir(self, repo_full_name: str, path: str, ref: str) -> list[str]:
        """List entry names in a directory at ``ref``."""
        return await self._repo_reads.list_dir(repo_full_name, path, ref)

    @translate_errors
    async def read_issue(self, repo_full_name: str, number: int) -> GitHubIssue:
        """Fetch an issue by number."""
        return await self._repo_reads.read_issue(repo_full_name, number)

    @translate_errors
    async def list_open_pull_requests(self, repo_full_name: str) -> list[GitHubPullRequest]:
        """List open pull requests for a repository."""
        return await self._repo_reads.list_open_pull_requests(repo_full_name)

    @translate_errors
    async def list_installation_repositories(self) -> list[GitHubRepository]:
        """List repositories the installation can access."""
        return await self._repo_reads.list_installation_repositories()

    @translate_errors
    async def user_can_trigger(
        self, repo_full_name: str, *, private: bool, user_login: str
    ) -> bool:
        """Return whether ``user_login`` may trigger a review (spec overview §4).

        Public repos require **write**; private repos require **read**. Any auth
        failure fails closed (``False``).
        """
        return await self._repo_reads.user_can_trigger(
            repo_full_name, private=private, user_login=user_login
        )
