"""Repository and installation read surface for the GitHub client.

A small composition helper: :class:`GitHubClient` constructs one with the shared
``GitHub`` client, :class:`ReadCache`, and :class:`ToolBudget`, then delegates the
repository-wide queries here. Keeping these out of the client module holds both
files under the size ceiling without implicit-attribute mixins.
"""

from __future__ import annotations

from githubkit import GitHub, TokenAuthStrategy

from slopolis_core.github._mapping import (
    decode_content,
    iso,
    permission_satisfies,
    raise_for_status,
    split_repo,
    truncate_bytes,
)
from slopolis_core.github.errors import GitHubError, GitHubNotFoundError
from slopolis_core.github.limits import MAX_FILE_BYTES, ReadCache, RepoMeta, ToolBudget
from slopolis_core.github.models import GitHubIssue, GitHubPullRequest, GitHubRepository
from slopolis_core.github.transport import translate_errors

__all__ = ["RepoReads"]

_TRIGGER_LEVELS = ("read", "write")


def _required_level(*, private: bool, required: str | None) -> str:
    """Resolve the permission a trigger needs (overview §4, spec 10.10).

    ``required`` is the workspace's per-repository override; ``None`` means no
    override is in force and the spec rule applies.
    """
    if required is None:
        return "read" if private else "write"
    if required not in _TRIGGER_LEVELS:
        raise ValueError(
            f"required must be one of {_TRIGGER_LEVELS} or None, got {required!r}"
        )
    return required


class RepoReads:
    """Repository-wide and installation-wide queries."""

    def __init__(
        self,
        github: GitHub[TokenAuthStrategy],
        *,
        cache: ReadCache,
        budget: ToolBudget,
    ) -> None:
        self._github = github
        self._cache = cache
        self.budget = budget

    @translate_errors
    async def fetch_repo_meta(self, repo_full_name: str) -> RepoMeta:
        """Fetch a repository's privacy and default branch."""
        owner, repo = split_repo(repo_full_name)
        response = await self._github.rest.repos.async_get(owner, repo)
        raise_for_status(response, f"repository {repo_full_name}")
        data = response.parsed_data
        return RepoMeta(private=data.private, default_branch=data.default_branch or "main")

    @translate_errors
    async def fetch_diff(self, repo_full_name: str, number: int) -> str:
        """Fetch a pull request's unified diff as raw text."""
        owner, repo = split_repo(repo_full_name)
        response = await self._github.rest.pulls.async_get(
            owner, repo, number, headers={"Accept": "application/vnd.github.v3.diff"}
        )
        raise_for_status(response, f"diff for {repo_full_name}#{number}")
        return response.text

    @translate_errors
    async def read_issue(self, repo_full_name: str, number: int) -> GitHubIssue:
        """Fetch an issue by number."""
        cached = self._cache.issues.get((repo_full_name, number))
        if cached is not None:
            return cached
        owner, repo = split_repo(repo_full_name)
        self.budget.consume(method="read_issue")
        response = await self._github.rest.issues.async_get(owner, repo, number)
        raise_for_status(response, f"issue {repo_full_name}#{number}")
        issue = response.parsed_data
        result = GitHubIssue(
            number=issue.number,
            title=issue.title,
            body=issue.body or "",
            state=issue.state,
        )
        self._cache.issues[(repo_full_name, number)] = result
        return result

    @translate_errors
    async def list_open_pull_requests(self, repo_full_name: str) -> list[GitHubPullRequest]:
        """List open pull requests for a repository."""
        cached = self._cache.open_pulls.get(repo_full_name)
        if cached is not None:
            return cached
        owner, repo = split_repo(repo_full_name)
        self.budget.consume(method="list_open_pull_requests")
        response = await self._github.rest.pulls.async_list(owner, repo, state="open", per_page=100)
        raise_for_status(response, f"open pull requests for {repo_full_name}")
        meta = await self.fetch_repo_meta(repo_full_name)
        result = [
            GitHubPullRequest(
                repo_full_name=repo_full_name,
                private=meta.private,
                number=pull.number,
                title=pull.title,
                url=pull.html_url,
                head_branch=pull.head.ref,
                base_branch=pull.base.ref,
                head_sha=pull.head.sha,
                default_branch=meta.default_branch,
                body=pull.body or "",
                author_login=pull.user.login if pull.user is not None else "",
                draft=pull.draft or False,
                changed_files=0,
                additions=0,
                deletions=0,
                updated_at=iso(pull.updated_at),
                created_at=iso(pull.created_at),
            )
            for pull in response.parsed_data
        ]
        self._cache.open_pulls[repo_full_name] = result
        return result

    @translate_errors
    async def compare_commits(self, repo_full_name: str, base: str, head: str) -> int:
        """Count the commits between two refs (``base...head``).

        The three-dot form compares from the merge base, so a head that moved
        past ``base`` counts exactly the commits it moved — which is what the
        inbox reports as "reviewed N commits ago" (spec v3 §2). A pair GitHub
        refuses (a force-pushed or unrelated SHA) raises
        :class:`GitHubNotFoundError`, so a caller can report an unknown distance
        instead of failing on it.
        """
        cached = self._cache.compares.get((repo_full_name, base, head))
        if cached is not None:
            return cached
        owner, repo = split_repo(repo_full_name)
        self.budget.consume(method="compare_commits")
        response = await self._github.rest.repos.async_compare_commits(
            owner, repo, f"{base}...{head}"
        )
        raise_for_status(response, f"commits between {base} and {head} in {repo_full_name}")
        total = response.parsed_data.total_commits
        self._cache.compares[(repo_full_name, base, head)] = total
        return total

    @translate_errors
    async def list_installation_repositories(self) -> list[GitHubRepository]:
        """List repositories the installation can access."""
        self.budget.consume(method="list_installation_repositories")
        response = await self._github.rest.apps.async_list_repos_accessible_to_installation(
            per_page=100
        )
        raise_for_status(response, "list installation repositories")
        return [
            GitHubRepository(
                id=repo.id,
                full_name=repo.full_name,
                private=repo.private,
                default_branch=repo.default_branch or "main",
                open_pr_count=repo.open_issues_count or 0,
                last_activity_at=iso(repo.updated_at or repo.pushed_at),
            )
            for repo in response.parsed_data.repositories
        ]

    @translate_errors
    async def read_file(self, repo_full_name: str, path: str, ref: str) -> str:
        """Fetch and decode a file at ``ref``, truncating past the byte cap."""
        cached = self._cache.contents.get((repo_full_name, path, ref))
        if cached is not None:
            return cached
        owner, repo = split_repo(repo_full_name)
        self.budget.consume(method="read_file")
        response = await self._github.rest.repos.async_get_content(owner, repo, path, ref=ref)
        raise_for_status(response, f"file {repo_full_name}:{path}@{ref}")
        data = response.parsed_data
        if isinstance(data, list) or data.type != "file":
            raise GitHubNotFoundError(f"{repo_full_name}:{path}@{ref} is not a file")
        result = truncate_bytes(decode_content(data), MAX_FILE_BYTES)
        self._cache.contents[(repo_full_name, path, ref)] = result
        return result

    @translate_errors
    async def list_dir(self, repo_full_name: str, path: str, ref: str) -> list[str]:
        """List entry names in a directory at ``ref``."""
        cached = self._cache.dirs.get((repo_full_name, path, ref))
        if cached is not None:
            return cached
        owner, repo = split_repo(repo_full_name)
        self.budget.consume(method="list_dir")
        response = await self._github.rest.repos.async_get_content(owner, repo, path, ref=ref)
        raise_for_status(response, f"directory {repo_full_name}:{path}@{ref}")
        data = response.parsed_data
        if not isinstance(data, list):
            raise GitHubNotFoundError(f"{repo_full_name}:{path}@{ref} is not a directory")
        names = [entry.name for entry in data]
        self._cache.dirs[(repo_full_name, path, ref)] = names
        return names

    @translate_errors
    async def user_can_trigger(
        self,
        repo_full_name: str,
        *,
        private: bool,
        user_login: str,
        required: str | None = None,
    ) -> bool:
        """Return whether ``user_login`` may trigger a review (overview §4).

        With no ``required`` the spec rule stands: public repos require
        **write**, private repos require **read**. A workspace override
        (spec 10.10) supplies the literal instead, loosening or tightening the
        rule per repository. Any auth failure fails closed (``False``); a
        literal outside the two values is a programming error.
        """
        level = _required_level(private=private, required=required)
        try:
            permission = await self._fetch_permission(repo_full_name, user_login)
        except GitHubError:
            return False
        return permission_satisfies(permission, required=level)

    @translate_errors
    async def _fetch_permission(self, repo_full_name: str, user_login: str) -> str:
        """Fetch a user's permission string, translating transport errors."""
        owner, repo = split_repo(repo_full_name)
        response = await self._github.rest.repos.async_get_collaborator_permission_level(
            owner, repo, user_login
        )
        raise_for_status(response, f"permission for {user_login} on {repo_full_name}")
        return response.parsed_data.permission
