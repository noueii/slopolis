"""Publisher: writes review results back to GitHub (spec 10.7).

Three surfaces, all idempotent across reruns:
- a **rolling summary comment**, found by its marker and edited in place,
- **inline line comments** anchored to the PR head commit, reconciled against
  what the pull request already holds so a retry does not post them twice,
- a **Check Run** named ``slopolis`` created or updated in place.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, cast

from githubkit import GitHub, TokenAuthStrategy

from slopolis_core.github._mapping import raise_for_status, split_repo
from slopolis_core.github.errors import GitHubError
from slopolis_core.github.models import InlineComment
from slopolis_core.github.transport import translate_errors

__all__ = ["CHECK_RUN_NAME", "SUMMARY_MARKER", "CheckConclusion", "GitHubPublisher"]

_LOG = logging.getLogger("slopolis_core.github.publisher")

#: Fixed check-run name so reruns find and update their own run.
CHECK_RUN_NAME = "slopolis"

#: First line of the rolling summary comment. One constant shared by the body
#: that carries the marker and the lookup that finds it again, so a rerun edits
#: the comment it posted instead of adding a second one (spec 10.7).
SUMMARY_MARKER = "## slopolis review"

#: How many comment pages a lookup walks — the summary thread and the PR's
#: review comments alike. One page covers every ordinary pull request; the bound
#: keeps a pathological thread from costing an unbounded number of requests.
_COMMENT_PAGE_SIZE = 100
_MAX_COMMENT_PAGES = 5

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


@dataclass(frozen=True, slots=True)
class _ExistingComment:
    """One review comment this App already has on a pull request."""

    id: int
    path: str
    line: int | None
    body: str


class GitHubPublisher:
    """Publishes summary, inline, and check-run results to GitHub.

    ``app_id`` is the App this publisher writes as: GitHub stamps it on every
    comment the installation token posts, and it is what tells a rerun's own
    summary comment apart from a user's quote of the same marker (spec 10.7).
    A caller that does not know it still gets a working publisher — the lookup
    then trusts the marker and an App author.

    ``app_login`` is the login those writes appear as (``<slug>[bot]``), and it is
    what reconciliation of line comments has to match: a review comment carries no
    ``performed_via_github_app`` field, only its author. Without it nothing is
    adopted, which publishes exactly what a reconciliation-free publisher does.
    """

    def __init__(
        self,
        github: GitHub[TokenAuthStrategy],
        *,
        app_id: int | None = None,
        app_login: str | None = None,
    ) -> None:
        self._github = github
        self._app_id = app_id
        self._app_login = app_login

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
        """Return the id of this pull request's newest rolling summary comment.

        The marker is deliberately not enough on its own: users quote the summary
        in the conversation, and editing one of those would rewrite a comment
        that is not ours. A comment counts as ours only when an App performed it
        — and, when this publisher knows which App it writes as, when that App is
        the one that performed it (spec 10.7).

        The newest match wins. A pull request can carry several — an earlier build
        posted one per publish — and the one holding the current state is the last
        one, so editing an older summary would resurrect a status the review has
        moved past and leave the current comment stale.
        """
        ref = _RepoRef.of(repo_full_name, number)
        found: int | None = None
        for page in range(1, _MAX_COMMENT_PAGES + 1):
            response = await self._github.rest.issues.async_list_comments(
                ref.owner,
                ref.repo,
                ref.number,
                per_page=_COMMENT_PAGE_SIZE,
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
                    found = comment.id
            if len(comments) < _COMMENT_PAGE_SIZE:
                break
        return found

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
    async def reconcile_inline_comments(
        self,
        repo_full_name: str,
        number: int,
        comments: list[InlineComment],
    ) -> list[int | None]:
        """The comment that already carries each of ``comments``, index-aligned.

        A run can die between posting a line comment and recording it, so before
        posting, this reads what the pull request already holds: a review comment
        on the same ``path`` and ``line`` written by this publisher's own App *is*
        that finding's comment, and its id is returned instead of a second copy of
        it. ``None`` means the comment is genuinely new and must be posted.

        The matching rule, and what it cannot do:

        - Location and author are all GitHub keeps. A finding the model re-worded
          since the comment was written is still that location's comment, so it is
          adopted and the body is replaced with the rendering this publish would
          post — the alternative is a comment saying something slopolis no longer
          means.
        - Two findings on one line cannot be told apart by GitHub, so comments and
          findings at one location are paired in the order each is in — the
          listing is oldest first, the publish's own order is the model's: the
          second finding posts its own comment rather than sharing the first's. A
          retry re-pairs them against the same listing order, so it converges on
          one comment per finding instead of stacking more.
        - A comment GitHub has moved off the diff carries no ``line`` and matches
          nothing, so that finding is posted afresh.
        - A publisher that does not know its own login adopts nothing: it cannot
          tell its comments from a human's or another App's, and adopting one of
          those would relabel someone else's words as this review's.

        A refused *read* propagates as :class:`GitHubError` so the caller can
        publish without reconciling; a refused *edit* does not — an adopted
        comment is still this App's comment on that line, merely not repaired yet.
        """
        if self._app_login is None:
            return [None] * len(comments)

        ref = _RepoRef.of(repo_full_name, number)
        available: dict[tuple[str, int | None], list[_ExistingComment]] = {}
        for existing in await self._list_own_review_comments(ref):
            available.setdefault((existing.path, existing.line), []).append(existing)

        adopted: list[int | None] = []
        for comment in comments:
            candidates = available.get((comment.path, comment.line))
            if not candidates:
                adopted.append(None)
                continue
            match = candidates.pop(0)
            if match.body != comment.body:
                await self._repair_review_comment(ref, match.id, comment.body)
            adopted.append(match.id)
        return adopted

    @translate_errors
    async def _list_own_review_comments(self, ref: _RepoRef) -> list[_ExistingComment]:
        """Every review comment on ``ref``'s pull request that this App wrote."""
        found: list[_ExistingComment] = []
        for page in range(1, _MAX_COMMENT_PAGES + 1):
            response = await self._github.rest.pulls.async_list_review_comments(
                ref.owner,
                ref.repo,
                ref.number,
                per_page=_COMMENT_PAGE_SIZE,
                page=page,
            )
            raise_for_status(
                response, f"list review comments on {ref.owner}/{ref.repo}#{ref.number}"
            )
            page_comments = response.parsed_data
            for comment in page_comments:
                if _comment_author_login(comment) != self._app_login:
                    continue
                line = comment.line
                found.append(
                    _ExistingComment(
                        id=comment.id,
                        path=comment.path,
                        line=line if isinstance(line, int) else None,
                        body=comment.body,
                    )
                )
            if len(page_comments) < _COMMENT_PAGE_SIZE:
                break
        return found

    async def _repair_review_comment(self, ref: _RepoRef, comment_id: int, body: str) -> None:
        """Replace one adopted comment's body; never raise into the publish.

        Best effort on purpose: the link is already sound (same App, same path and
        line), so a repair GitHub refuses costs the comment only its new wording.
        Raising here would fail a publish whose comments are on the pull request.
        """
        try:
            await self._update_review_comment(ref, comment_id, body)
        except GitHubError as exc:
            _LOG.warning(
                "could not repair a review comment; it keeps its older body",
                extra={
                    "repo": f"{ref.owner}/{ref.repo}",
                    "number": ref.number,
                    "comment_id": comment_id,
                    "reason": f"{type(exc).__name__}: {exc}",
                },
            )

    @translate_errors
    async def _update_review_comment(self, ref: _RepoRef, comment_id: int, body: str) -> None:
        response = await self._github.rest.pulls.async_update_review_comment(
            ref.owner, ref.repo, comment_id, data={"body": body}
        )
        raise_for_status(response, f"repair review comment {comment_id}")

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


def _comment_author_login(comment: object) -> str | None:
    """The login that authored a review comment, or ``None`` when it has none.

    Read structurally for the reason :func:`_performing_app_id` is: githubkit's
    ``UNSET`` sentinel stands in for an absent field, and only issue comments
    carry ``performed_via_github_app`` — a review comment identifies its App
    solely through the author login.
    """
    user = getattr(comment, "user", None)
    login = getattr(user, "login", None)
    return login if isinstance(login, str) else None


def _performing_app_id(comment: object) -> int | None:
    """The App id that performed ``comment``, or ``None`` when no App did.

    Read structurally on purpose: githubkit marks an absent optional field with
    its own ``UNSET`` sentinel instead of ``None``, and the model class that
    carries it is not type-importable from here.
    """
    performed_by = getattr(comment, "performed_via_github_app", None)
    app_id = getattr(performed_by, "id", None)
    return app_id if isinstance(app_id, int) else None
