"""Publisher: writes review results back to GitHub (spec 10.7).

Three surfaces, all idempotent across reruns:
- a **rolling summary comment** edited in place when an id is supplied,
- **inline line comments** anchored to the PR head commit,
- a **Check Run** named ``slopolis`` created or updated in place.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, cast

import githubkit.auth
from githubkit import GitHub, TokenAuthStrategy

from slopolis_core.github._mapping import raise_for_status, split_repo
from slopolis_core.github.errors import GitHubError
from slopolis_core.github.models import InlineComment
from slopolis_core.github.transport import translate_errors

__all__ = ["CHECK_RUN_NAME", "CheckConclusion", "GitHubPublisher"]

#: Fixed check-run name so reruns find and update their own run.
CHECK_RUN_NAME = "slopolis"

#: Conclusions slopolis is allowed to publish.
CheckConclusion = Literal["success", "failure", "neutral"]

_VALID_CONCLUSIONS: frozenset[str] = frozenset({"success", "failure", "neutral"})


@dataclass(frozen=True, slots=True)
class _RepoRef:
    """Resolved owner/repo/number for a comment operation."""

    owner: str
    repo: str
    number: int

    @classmethod
    def of(cls, repo_full_name: str, number: int) -> _RepoRef:
        owner, repo = split_repo(repo_full_name)
        return cls(owner=owner, repo=repo, number=number)


class GitHubPublisher:
    """Publishes summary, inline, and check-run results to GitHub."""

    def __init__(
        self,
        github: GitHub[TokenAuthStrategy],
        *,
        app_client: GitHub[githubkit.auth.AppAuthStrategy] | None = None,
    ) -> None:
        self._github = github
        self._app_client = app_client

    @classmethod
    def from_installation_token(cls, token: str) -> GitHubPublisher:
        """Build a publisher from an installation access token."""
        return cls(GitHub(TokenAuthStrategy(token)))

    @translate_errors
    async def upsert_summary_comment(
        self,
        repo_full_name: str,
        number: int,
        body: str,
        existing_comment_id: int | None,
    ) -> int:
        """Create or edit the rolling summary comment; return its id."""
        ref = _RepoRef.of(repo_full_name, number)
        if existing_comment_id is None:
            response = await self._github.rest.issues.async_create_comment(
                ref.owner, ref.repo, ref.number, data={"body": body}
            )
            raise_for_status(response, f"create summary comment on {repo_full_name}#{number}")
            return response.parsed_data.id

        response = await self._github.rest.issues.async_update_comment(
            ref.owner, ref.repo, existing_comment_id, data={"body": body}
        )
        raise_for_status(response, f"edit summary comment {existing_comment_id}")
        return response.parsed_data.id

    @translate_errors
    async def post_inline_comments(
        self,
        repo_full_name: str,
        number: int,
        comments: list[InlineComment],
        commit_id: str,
    ) -> list[int]:
        """Post one review comment per finding; return the created ids."""
        ref = _RepoRef.of(repo_full_name, number)
        created: list[int] = []
        for comment in comments:
            response = await self._github.rest.pulls.async_create_review_comment(
                ref.owner,
                ref.repo,
                ref.number,
                data={
                    "body": comment.body,
                    "path": comment.path,
                    "line": comment.line,
                    "side": "RIGHT",
                    "commit_id": commit_id,
                },
            )
            raise_for_status(response, f"inline comment on {comment.path}:{comment.line}")
            created.append(response.parsed_data.id)
        return created

    @translate_errors
    async def upsert_check_run(
        self,
        repo_full_name: str,
        head_sha: str,
        *,
        conclusion: str,
        title: str,
        summary: str,
    ) -> int:
        """Create or update the ``slopolis`` check run; return its id.

        ``conclusion`` must be ``success``, ``failure``, or ``neutral``.
        """
        if conclusion not in _VALID_CONCLUSIONS:
            raise GitHubError(
                f"Invalid check-run conclusion {conclusion!r}; "
                "expected one of: failure, neutral, success"
            )
        owner, repo = split_repo(repo_full_name)
        valid_conclusion = cast("CheckConclusion", conclusion)
        existing = await self._find_existing_check_run(owner, repo, head_sha)
        completed_at = datetime.now(UTC)
        if existing is None:
            response = await self._github.rest.checks.async_create(
                owner,
                repo,
                data={
                    "name": CHECK_RUN_NAME,
                    "head_sha": head_sha,
                    "status": "completed",
                    "conclusion": valid_conclusion,
                    "completed_at": completed_at,
                    "output": {"title": title, "summary": summary},
                },
            )
            raise_for_status(response, f"create check run on {repo_full_name}")
            return response.parsed_data.id

        response = await self._github.rest.checks.async_update(
            owner,
            repo,
            existing,
            data={
                "status": "completed",
                "conclusion": valid_conclusion,
                "completed_at": completed_at,
                "output": {"title": title, "summary": summary},
            },
        )
        raise_for_status(response, f"update check run on {repo_full_name}")
        return response.parsed_data.id

    @translate_errors
    async def _find_existing_check_run(self, owner: str, repo: str, head_sha: str) -> int | None:
        """Return the id of the existing ``slopolis`` run for ``head_sha``."""
        response = await self._github.rest.checks.async_list_for_ref(
            owner, repo, head_sha, check_name=CHECK_RUN_NAME, filter_="latest"
        )
        raise_for_status(response, f"list check runs for {owner}/{repo}@{head_sha}")
        for run in response.parsed_data.check_runs:
            if run.name == CHECK_RUN_NAME:
                return run.id
        return None
