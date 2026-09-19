"""Connected repositories and their open pull requests.

Repository rows come from the workspace DB; open pull requests are fetched
live from GitHub through the app-owned client and mapped onto the wire shape,
including rolled-up CI checks. A repository the workspace does not own is a 404
in the standard error envelope, matching the mock.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence

from fastapi import APIRouter, Request
from sqlalchemy import select

from app.deps import DbSessionDep, WorkspaceIdDep
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
from slopolis_core.github.models import GitHubPullRequest
from slopolis_db.models import Repository

__all__ = ["router"]

router = APIRouter(prefix="/repositories", tags=["repositories"])


@router.get("")
async def list_repositories(
    db: DbSessionDep, workspace_id: WorkspaceIdDep
) -> RepositoryListResponse:
    """Return every repository connected to the workspace."""
    rows = list(
        (
            await db.scalars(
                select(Repository)
                .where(Repository.workspace_id == workspace_id)
                .order_by(Repository.full_name)
            )
        ).all()
    )
    return RepositoryListResponse(items=[_summary_from_row(row) for row in rows])


@router.get("/{owner}/{name}/pulls")
async def list_repository_pulls(
    owner: str,
    name: str,
    request: Request,
    db: DbSessionDep,
    workspace_id: WorkspaceIdDep,
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

    client = _client_from_app(request)
    pulls = await _list_open_pulls(client, full_name)
    return RepositoryPullRequestsResponse(
        repository=_summary_from_row(row, open_pr_count=len(pulls)),
        pull_requests=[_open_pull_request(pull) for pull in pulls],
    )


def _client_from_app(request: Request) -> GitHubClient:
    """Return the app-owned GitHub client, or fail with a config error."""
    client: GitHubClient | None = getattr(request.app.state, "github_client", None)
    if client is None:
        raise ApiError(
            503,
            "github_not_configured",
            "The GitHub App is not configured for this workspace.",
        )
    return client


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


def _open_pull_request(pull: GitHubPullRequest) -> OpenPullRequest:
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
        checks=PullRequestChecks(state="none", total=0, passing=0),
    )
