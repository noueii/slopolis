"""Connected repositories and their open pull requests.

Repository rows come from the workspace DB; open pull requests are fetched live
from GitHub through this request's client and mapped onto the wire shape,
including real diff size and rolled-up CI checks. GitHub reports diff size and
check state per pull request only, so one listing costs one read per pull
request. A repository the workspace does not own is a 404 in the standard error
envelope, matching the mock.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from collections.abc import Sequence

from fastapi import APIRouter
from sqlalchemy import select

from app.deps import (
    DbSessionDep,
    OptionalGitHubClientDep,
    WorkspaceIdDep,
)
from app.errors import ApiError
from app.schemas import (
    OpenPullRequest,
    PullRequestChecks,
    RepositoryListResponse,
    RepositoryPullRequestsResponse,
    RepositoryRef,
    RepositorySummary,
    UserRef,
)
from slopolis_core.github.client import GitHubClient
from slopolis_core.github.errors import GitHubError, GitHubNotFoundError
from slopolis_core.github.models import CheckRun, GitHubPullRequest
from slopolis_db.models import Repository

__all__ = ["router"]

_logger = logging.getLogger(__name__)

router = APIRouter(prefix="/repositories", tags=["repositories"])

#: GitHub reads a single request may have in flight at once.
_MAX_CONCURRENT_READS = 8

#: Check-run conclusions that count as green.
_PASSING_CONCLUSIONS = frozenset({"success", "neutral", "skipped"})

#: Check-run conclusions that make the rollup fail.
_FAILING_CONCLUSIONS = frozenset(
    {"failure", "timed_out", "cancelled", "action_required", "startup_failure"}
)


@router.get("")
async def list_repositories(
    db: DbSessionDep,
    workspace_id: WorkspaceIdDep,
    client: OptionalGitHubClientDep,
) -> RepositoryListResponse:
    """Return every repository connected to the workspace, with its open count."""
    rows = list(
        (
            await db.scalars(
                select(Repository)
                .where(Repository.workspace_id == workspace_id)
                .order_by(Repository.full_name)
            )
        ).all()
    )
    counts = (
        await _open_pull_counts(client, [row.full_name for row in rows])
        if client is not None
        else {}
    )
    return RepositoryListResponse(
        items=[
            _summary_from_row(row, open_pr_count=counts.get(row.full_name, 0))
            for row in rows
        ]
    )


@router.get("/{owner}/{name}/pulls")
async def list_repository_pulls(
    owner: str,
    name: str,
    db: DbSessionDep,
    workspace_id: WorkspaceIdDep,
    client: OptionalGitHubClientDep,
) -> RepositoryPullRequestsResponse:
    """Return the open pull requests for one connected repository."""
    full_name = f"{owner}/{name}"
    row = await db.scalar(
        select(Repository).where(
            Repository.workspace_id == workspace_id,
            Repository.full_name == full_name,
        )
    )
    if row is None:
        raise ApiError(
            404,
            "repository_not_found",
            f"{full_name} is not connected to this workspace.",
        )
    if client is None:
        # The repository is known, but answering for it needs GitHub.
        raise ApiError(
            503,
            "github_not_configured",
            "The GitHub App is not configured for this workspace.",
        )

    pulls = await _list_open_pulls(client, full_name)
    return RepositoryPullRequestsResponse(
        repository=_summary_from_row(row, open_pr_count=len(pulls)),
        pull_requests=await _open_pull_requests(client, full_name, pulls),
    )


async def _list_open_pulls(
    client: GitHubClient, full_name: str
) -> Sequence[GitHubPullRequest]:
    """Fetch open pull requests, translating typed GitHub failures."""
    try:
        return await client.list_open_pull_requests(full_name)
    except GitHubNotFoundError as exc:
        raise ApiError(
            404,
            "repository_not_found",
            "The repository was not found.",
            detail=str(exc),
        ) from exc
    except GitHubError as exc:
        raise ApiError(
            502,
            "pull_requests_unavailable",
            "Could not load open pull requests.",
            detail=str(exc),
        ) from exc


async def _open_pull_requests(
    client: GitHubClient, full_name: str, pulls: Sequence[GitHubPullRequest]
) -> list[OpenPullRequest]:
    """Map open pull requests onto the selection surface, with their real numbers.

    GitHub's listing omits diff size and CI state, so each pull request is read
    individually — concurrently, but never more than
    :data:`_MAX_CONCURRENT_READS` at a time.
    """
    semaphore = asyncio.Semaphore(_MAX_CONCURRENT_READS)

    async def one(pull: GitHubPullRequest) -> OpenPullRequest:
        async with semaphore:
            detail = await client.get_pull_request(full_name, pull.number)
            runs = await client.list_check_runs(full_name, detail.head_sha)
        return _open_pull_request(detail, checks=_checks_rollup(runs))

    try:
        return list(await asyncio.gather(*(one(pull) for pull in pulls)))
    except GitHubNotFoundError as exc:
        raise ApiError(
            404,
            "repository_not_found",
            "The repository was not found.",
            detail=str(exc),
        ) from exc
    except GitHubError as exc:
        raise ApiError(
            502,
            "pull_requests_unavailable",
            "Could not load open pull requests.",
            detail=str(exc),
        ) from exc


async def _open_pull_counts(
    client: GitHubClient, full_names: Sequence[str]
) -> dict[str, int]:
    """Open pull-request counts per repository, concurrently but bounded.

    One repository the App cannot read must not fail the whole list: its count
    falls back to 0 and the failure is logged.
    """
    semaphore = asyncio.Semaphore(_MAX_CONCURRENT_READS)

    async def count(full_name: str) -> tuple[str, int]:
        async with semaphore:
            try:
                pulls = await client.list_open_pull_requests(full_name)
            except GitHubError as exc:
                _logger.warning(
                    "open pull requests unavailable for %s: %s", full_name, exc
                )
                return full_name, 0
            return full_name, len(pulls)

    return dict(await asyncio.gather(*(count(name) for name in full_names)))


def _summary_from_row(row: Repository, *, open_pr_count: int = 0) -> RepositorySummary:
    """Map a repository row onto the summary shape."""
    activity = row.last_activity_at or row.created_at
    if activity.tzinfo is None:
        activity = activity.replace(tzinfo=dt.UTC)
    return RepositorySummary(
        id=str(row.id),
        full_name=row.full_name,
        private=row.private,
        default_branch=row.default_branch,
        open_pr_count=open_pr_count,
        last_activity_at=activity,
        connected=row.connected,
    )


def _checks_rollup(runs: Sequence[CheckRun]) -> PullRequestChecks:
    """Roll a head commit's check runs up into the CI hint the picker shows."""
    passing = sum(1 for run in runs if run.conclusion in _PASSING_CONCLUSIONS)
    if any(run.conclusion in _FAILING_CONCLUSIONS for run in runs):
        state = "failing"
    elif any(run.status != "completed" for run in runs):
        state = "pending"
    elif runs:
        state = "passing"
    else:
        state = "none"
    return PullRequestChecks(state=state, total=len(runs), passing=passing)


def _open_pull_request(
    pull: GitHubPullRequest, *, checks: PullRequestChecks
) -> OpenPullRequest:
    """Map one GitHub pull request onto the selection-surface shape."""
    return OpenPullRequest(
        id=f"pr_{pull.repo_full_name.replace('/', '_')}_{pull.number}",
        repository=RepositoryRef(
            id=f"repo_{pull.repo_full_name.lower().replace('/', '_')}",
            full_name=pull.repo_full_name,
            private=pull.private,
            default_branch=pull.default_branch,
        ),
        number=pull.number,
        title=pull.title,
        url=pull.url,
        author=UserRef(
            id=f"usr_{pull.author_login}",
            handle=pull.author_login,
            name=pull.author_login,
        ),
        updated_at=pull.updated_at,
        draft=pull.draft,
        comments=0,
        changed_files=pull.changed_files,
        additions=pull.additions,
        deletions=pull.deletions,
        checks=checks,
    )
