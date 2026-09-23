"""Connected repositories and their workspace switches.

Repository rows come from the workspace DB; the open pull-request count each row
carries is fetched live from GitHub through the client for **that repository's own
installation**. A repository the workspace does not own is a 404 in the standard
error envelope, matching the mock.

The workspace also owns the **enable switch** (spec 10.1): ``PATCH
/repositories/{id}`` parks a connected repository — it stays listed with its
history and pre-flight refuses its pull requests — or brings it back. Both
transitions are audited.

The count is memoized per app for a few seconds
(:data:`~app.routers._pull_reads.PULL_CACHE_TTL_SECONDS`), so mounting the screen
twice does not spend GitHub rate limit twice. The one visible consequence: **the
list may be up to 30s stale about PR counts.**

The open pull requests themselves are not served here: the workspace-wide inbox
(``GET /api/pull-requests``, spec v3 §6) supersedes the per-repository picker this
router used to answer with.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import uuid
from collections.abc import Sequence
from typing import Any

from fastapi import APIRouter, Request
from pydantic import ConfigDict, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import CAMEL
from app.deps import (
    CurrentUserDep,
    DbSessionDep,
    WorkspaceIdDep,
    WorkspaceRepositoriesDep,
)
from app.errors import ApiError
from app.routers._pull_reads import (
    MAX_CONCURRENT_READS,
    CacheKey,
    app_cache,
    cache_key,
)
from app.schemas import (
    RepositoryListResponse,
    RepositorySummary,
    RequiredAccess,
    WireModel,
)
from app.services.github_clients import WorkspaceRepositories
from slopolis_core.cache import TTLCache
from slopolis_core.github.client import GitHubClient
from slopolis_core.github.errors import GitHubError
from slopolis_db.models import AuditLog, GitHubInstallation, Repository

__all__ = ["router"]

_logger = logging.getLogger(__name__)

router = APIRouter(prefix="/repositories", tags=["repositories"])

#: ``app.state`` name for the open-count cache, so a deployment can swap it.
_PULL_COUNTS_CACHE = "pull_counts_cache"


@router.get("")
async def list_repositories(
    request: Request,
    repositories: WorkspaceRepositoriesDep,
) -> RepositoryListResponse:
    """Return every repository connected to the workspace, with its open count.

    A workspace can hold several installations, so the rows are grouped by the
    installation that grants them and each group is read through its own client;
    the union, in name order, is what the caller sees. A group whose
    installation cannot be read keeps its rows without a live count.
    """
    groups = await repositories.by_installation()
    counts = await _open_pull_counts_by_installation(request, repositories, groups)
    rows = sorted(
        (row for _installation, group_rows in groups for row in group_rows),
        key=lambda row: row.full_name,
    )
    return RepositoryListResponse(
        items=[
            _summary_from_row(row, open_pr_count=counts.get(row.full_name, 0))
            for row in rows
        ]
    )


class RepositoryUpdateRequest(WireModel):
    """Body of ``PATCH /api/repositories/{id}`` — the workspace's own switch.

    Lives here rather than in ``app.schemas`` because it is the only body this
    router owns; it speaks the same camelCase wire shape as every other model.
    Public because it names a component of the published OpenAPI document.

    ``enabled`` is required: it is the one decision the endpoint carries, so a
    body without it has nothing to do and is refused rather than silently
    accepted.
    """

    model_config = ConfigDict(
        alias_generator=CAMEL,
        populate_by_name=True,
        extra="forbid",
    )

    enabled: bool | None = None
    #: The access override pre-flight applies (spec 10.10); the three literals
    #: are the whole vocabulary, so anything else is a 422.
    required_access: RequiredAccess | None = None

    @model_validator(mode="after")
    def _at_least_one_switch(self) -> RepositoryUpdateRequest:
        """Refuse a body that carries no switch at all."""
        if self.enabled is None and self.required_access is None:
            raise ValueError("supply enabled and/or requiredAccess")
        return self


@router.patch("/{repository_id}")
async def update_repository(
    repository_id: uuid.UUID,
    body: RepositoryUpdateRequest,
    request: Request,
    db: DbSessionDep,
    workspace_id: WorkspaceIdDep,
    repositories: WorkspaceRepositoriesDep,
    user: CurrentUserDep,
) -> RepositorySummary:
    """Park, re-enable, or re-policy one repository, returning its summary.

    Parking is how a workspace stops reviewing a repository GitHub still grants:
    the row — and with it every session and finding in its history — stays, and
    pre-flight refuses its pull requests. ``requiredAccess`` is the other
    per-repository decision (spec 10.10): the access rule pre-flight applies to
    its pull requests. Both switches are audited independently, and a field left
    as it already is changes nothing (no write, no audit row, same body), so
    retrying a toggle is safe. Another workspace's id is a 404.
    """
    row = await db.scalar(
        select(Repository).where(
            Repository.id == repository_id,
            Repository.workspace_id == workspace_id,
        )
    )
    if row is None:
        raise ApiError(
            404,
            "repository_not_found",
            "The repository is not connected to this workspace.",
        )

    changed = False
    if body.enabled is not None and row.enabled != body.enabled:
        row.enabled = body.enabled
        _audit(
            db,
            workspace_id=workspace_id,
            actor_id=user.id,
            action="repository.enabled" if body.enabled else "repository.disabled",
            target_type="repository",
            target_id=row.id,
        )
        changed = True
    if body.required_access is not None and row.required_access != body.required_access:
        row.required_access = body.required_access
        _audit(
            db,
            workspace_id=workspace_id,
            actor_id=user.id,
            action="repository.access_updated",
            target_type="repository",
            target_id=row.id,
            detail={"requiredAccess": row.required_access},
        )
        changed = True
    if changed:
        await db.commit()
        await db.refresh(row)

    return _summary_from_row(
        row, open_pr_count=await _open_count_for(request, repositories, row)
    )


def _audit(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    actor_id: uuid.UUID,
    action: str,
    target_type: str,
    target_id: uuid.UUID | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    """Stage one audit row for the caller to commit with its mutation."""
    db.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_user_id=actor_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            detail=detail,
        )
    )


async def _open_count_for(
    request: Request, repositories: WorkspaceRepositories, row: Repository
) -> int:
    """The repository's live open count, or 0 when its installation is unreadable.

    Served through the same cache the listing uses, so the summary a toggle
    answers with matches the row the caller is looking at.
    """
    resolved = await repositories.resolve(row.full_name)
    if resolved is None or resolved.client is None:
        return 0
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_READS)
    counts = await _cached_open_pull_counts(
        request, resolved.client, [row.full_name], semaphore
    )
    return counts.get(row.full_name, 0)


async def _open_pull_counts_by_installation(
    request: Request,
    repositories: WorkspaceRepositories,
    groups: Sequence[tuple[GitHubInstallation, list[Repository]]],
) -> dict[str, int]:
    """Open pull-request counts for every group, read concurrently but bounded.

    The bound is shared across installations, so several installations do not
    multiply the GitHub reads one listing may have in flight. An installation
    that cannot be read contributes nothing: its rows still list, with no live
    count, and nothing is cached for them.
    """
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_READS)

    async def read(
        installation: GitHubInstallation, rows: Sequence[Repository]
    ) -> dict[str, int]:
        client = await repositories.client_for_installation(installation)
        if client is None:
            return {}
        return await _cached_open_pull_counts(
            request, client, [row.full_name for row in rows], semaphore
        )

    counts: dict[str, int] = {}
    for part in await asyncio.gather(
        *(read(installation, rows) for installation, rows in groups)
    ):
        counts.update(part)
    return counts


async def _cached_open_pull_counts(
    request: Request,
    client: GitHubClient,
    full_names: Sequence[str],
    semaphore: asyncio.Semaphore,
) -> dict[str, int]:
    """Open pull-request counts per repository, cached per installation.

    Only repositories without a live entry are read, so mounting the screen
    repeatedly costs one read per repository per
    :data:`~app.routers._pull_reads.PULL_CACHE_TTL_SECONDS` instead of one per
    mount. A count GitHub refused is never cached, so the next request retries it.
    """
    cache: TTLCache[CacheKey, int] = app_cache(request, _PULL_COUNTS_CACHE)
    counts: dict[str, int] = {}
    unread: list[str] = []
    for full_name in full_names:
        hit = cache.get(cache_key(client, full_name))
        if hit is None:
            unread.append(full_name)
        else:
            counts[full_name] = hit
    if not unread:
        return counts
    read = await _open_pull_counts(client, unread, semaphore)
    for full_name, count in read.items():
        if count is None:
            counts[full_name] = 0
        else:
            cache.put(cache_key(client, full_name), count)
            counts[full_name] = count
    return counts


async def _open_pull_counts(
    client: GitHubClient, full_names: Sequence[str], semaphore: asyncio.Semaphore
) -> dict[str, int | None]:
    """Open pull-request counts per repository, concurrently but bounded.

    One repository the App cannot read must not fail the whole list: its count
    is ``None`` — the caller falls back to 0 — and the failure is logged so it
    is not mistaken for a real zero.
    """

    async def count(full_name: str) -> tuple[str, int | None]:
        async with semaphore:
            try:
                pulls = await client.list_open_pull_requests(full_name)
            except GitHubError as exc:
                _logger.warning(
                    "open pull requests unavailable for %s: %s", full_name, exc
                )
                return full_name, None
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
        enabled=row.enabled,
        required_access=row.required_access,
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
