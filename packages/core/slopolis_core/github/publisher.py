"""Publisher: writes review results back to GitHub (spec 10.7).

Three surfaces, all idempotent across reruns:
- a **rolling summary comment**, found by its marker and edited in place,
- **inline line comments** anchored to the PR head commit,
- a **Check Run** named ``slopolis`` created or updated in place.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, cast

from githubkit import GitHub, TokenAuthStrategy

from slopolis_core.github._mapping import raise_for_status, split_repo
from slopolis_core.github.errors import GitHubError
from slopolis_core.github.models import InlineComment
from slopolis_core.github.transport import translate_errors

__all__ = ["CHECK_RUN_NAME", "SUMMARY_MARKER", "CheckConclusion", "GitHubPublisher"]

#: Fixed check-run name so reruns find and update their own run.
CHECK_RUN_NAME = "slopolis"

#: First line of the rolling summary comment. One constant shared by the body
#: that carries the marker and the lookup that finds it again, so a rerun edits
#: the comment it posted instead of adding a second one (spec 10.7).
SUMMARY_MARKER = "## slopolis review"

#: How many comment pages the summary lookup walks. One page covers every
#: ordinary pull request; the bound keeps a pathological thread from costing an
#: unbounded number of requests.
_SUMMARY_PAGE_SIZE = 100
_MAX_SUMMARY_PAGES = 5

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
    """Publishes summary, inline, and check-run results to GitHub.

    ``app_id`` is the App this publisher writes as: GitHub stamps it on every
    comment the installation token posts, and it is what tells a rerun's own
    summary comment apart from a user's quote of the same marker (spec 10.7).
    A caller that does not know it still gets a working publisher — the lookup
    then trusts the marker and an App author.
    """

    def __init__(
        self,
        github: GitHub[TokenAuthStrategy],
        *,
        app_id: int | None = None,
    ) -> None:
        self._github = github
        self._app_id = app_id

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
    async def find_summary_comment(self, repo_full_name: str, number: int) -> int | None:
        """Return the id of this pull request's rolling summary comment, if any.

        The marker is deliberately not enough on its own: users quote the summary
        in the conversation, and editing one of those would rewrite a comment
        that is not ours. A comment counts as ours only when an App performed it
        — and, when this publisher knows which App it writes as, when that App is
        the one that performed it (spec 10.7).
        """
        ref = _RepoRef.of(repo_full_name, number)
        for page in range(1, _MAX_SUMMARY_PAGES + 1):
            response = await self._github.rest.issues.async_list_comments(
                ref.owner,
                ref.repo,
                ref.number,
                per_page=_SUMMARY_PAGE_SIZE,
                page=page,
            )
            raise_for_status(response, f"list comments on {repo_full_name}#{number}")
            comments = response.parsed_data
            for comment in comments:
                body = comment.body
                if not isinstance(body, str) or not body.startswith(SUMMARY_MARKER):
                    continue
                performer = _performing_app_id(comment)
                if performer is not None and (self._app_id is None or performer == self._app_id):
                    return comment.id
            if len(comments) < _SUMMARY_PAGE_SIZE:
                break
        return None

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


def _performing_app_id(comment: object) -> int | None:
    """The App id that performed ``comment``, or ``None`` when no App did.

    Read structurally on purpose: githubkit marks an absent optional field with
    its own ``UNSET`` sentinel instead of ``None``, and the model class that
    carries it is not type-importable from here.
    """
    performed_by = getattr(comment, "performed_via_github_app", None)
    app_id = getattr(performed_by, "id", None)
    return app_id if isinstance(app_id, int) else None
