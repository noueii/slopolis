"""Connected repositories and their open pull requests.

Repository rows come from the workspace DB; open pull requests are fetched live
from GitHub through the client for **each repository's own installation** and
mapped onto the wire shape, including real diff size and rolled-up CI checks.
GitHub reports diff size and check state per pull request only, so one listing
costs one read per pull request. A repository the workspace does not own is a 404
in the standard error envelope, matching the mock.

The workspace also owns the **enable switch** (spec 10.1): ``PATCH
/repositories/{id}`` parks a connected repository — it stays listed with its
history and pre-flight refuses its pull requests — or brings it back. Both
transitions are audited.

Both of those reads are memoized per app for a few seconds
(:data:`_PULL_CACHE_TTL_SECONDS`), so mounting the picker twice does not spend
GitHub rate limit twice. The one visible consequence: **the picker may be up to
30s stale about PR counts and CI state.**
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
from app.schemas import (
    OpenPullRequest,
    PullRequestChecks,
    RepositoryListResponse,
    RepositoryPullRequestsResponse,
    RepositoryRef,
    RepositorySummary,
    RequiredAccess,
    UserRef,
    WireModel,
)
from app.services.github_clients import WorkspaceRepositories
from slopolis_core.cache import TTLCache
from slopolis_core.github.client import GitHubClient
from slopolis_core.github.errors import GitHubError, GitHubNotFoundError
from slopolis_core.github.models import CheckRun, GitHubPullRequest
from slopolis_db.models import AuditLog, GitHubInstallation, Repository

__all__ = ["router"]

_logger = logging.getLogger(__name__)

router = APIRouter(prefix="/repositories", tags=["repositories"])

#: GitHub reads a single request may have in flight at once.
_MAX_CONCURRENT_READS = 8

#: How long a picker answer may be served without asking GitHub again. Long
#: enough to absorb a page's worth of mounts, short enough that a review the
#: user just pushed to shows up while they are still looking at the picker.
_PULL_CACHE_TTL_SECONDS = 30.0

#: ``app.state`` names for the two caches, so a deployment can swap them.
_PULL_COUNTS_CACHE = "pull_counts_cache"
_OPEN_PULLS_CACHE = "open_pulls_cache"

#: Check-run conclusions that count as green.
_PASSING_CONCLUSIONS = frozenset({"success", "neutral", "skipped"})

#: Check-run conclusions that make the rollup fail.
_FAILING_CONCLUSIONS = frozenset(
    {"failure", "timed_out", "cancelled", "action_required", "startup_failure"}
)


#: A cache entry is scoped to the installation and repository it was read through.
type _CacheKey = tuple[int | None, str]


def _cache_key(client: GitHubClient, full_name: str) -> _CacheKey:
    """Scope a cache entry to the installation and repository that produced it.

    A repository re-pointed at another installation can never be served the old
    installation's answer, and a revoke cannot leak into a new install. The test
    seam's fake client carries no installation id, which still keys per
    repository.
    """
    return getattr(client, "installation_id", None), full_name


def _app_cache[V](request: Request, name: str) -> TTLCache[_CacheKey, V]:
    """Return the app's cache called ``name``, creating it on first use.

    The cache hangs off the app rather than a module global so test apps do not
    share entries and a deployment can replace it with its own TTL.
    """
    cache: TTLCache[_CacheKey, V] | None = getattr(request.app.state, name, None)
    if cache is None:
        cache = TTLCache(ttl_seconds=_PULL_CACHE_TTL_SECONDS)
        setattr(request.app.state, name, cache)
    return cache


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


@router.get("/{owner}/{name}/pulls")
async def list_repository_pulls(
    owner: str,
    name: str,
    request: Request,
    repositories: WorkspaceRepositoriesDep,
) -> RepositoryPullRequestsResponse:
    """Return the open pull requests for one connected repository."""
    full_name = f"{owner}/{name}"
    resolved = await repositories.resolve(full_name)
    if resolved is None:
        raise ApiError(
            404,
            "repository_not_found",
            f"{full_name} is not connected to this workspace.",
        )
    client = resolved.client
    if client is None:
        # The repository is known, but reading it needs its installation.
        raise ApiError(
            503,
            "github_not_configured",
            "The GitHub App is not configured for this workspace.",
        )

    pull_requests = await _cached_open_pulls(request, client, full_name)
    return RepositoryPullRequestsResponse(
        repository=_summary_from_row(
            resolved.row, open_pr_count=len(pull_requests)
        ),
        pull_requests=pull_requests,
    )


class RepositoryUpdateRequest(WireModel):
    """Body of ``PATCH /api/repositories/{id}`` — the workspace's own switches.

    Lives here rather than in ``app.schemas`` because it is the only body this
    router owns; it speaks the same camelCase wire shape as every other model.
    Public because it names a component of the published OpenAPI document.

    Both fields are optional but at least one must be supplied: omitting a field
    leaves it as it is, while the two switches are unrelated, so an empty body
    has nothing to do and is refused rather than silently accepted.
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
    semaphore = asyncio.Semaphore(_MAX_CONCURRENT_READS)
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
    semaphore = asyncio.Semaphore(_MAX_CONCURRENT_READS)

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


async def _cached_open_pulls(
    request: Request, client: GitHubClient, full_name: str
) -> list[OpenPullRequest]:
    """The repository's open pull requests, mapped, served from the cache.

    These are the expensive reads — diff size and check runs, one pair per pull
    request — so a hit touches GitHub not at all, and the CI state it reports may
    be up to :data:`_PULL_CACHE_TTL_SECONDS` seconds old. Failures propagate
    unmapped, so nothing is cached for a repository GitHub would not answer for.
    """
    cache: TTLCache[_CacheKey, list[OpenPullRequest]] = _app_cache(
        request, _OPEN_PULLS_CACHE
    )
    key = _cache_key(client, full_name)
    hit = cache.get(key)
    if hit is not None:
        # Hand out a copy: the cached list must survive whatever the caller does.
        return list(hit)
    pulls = await _list_open_pulls(client, full_name)
    mapped = await _open_pull_requests(client, full_name, pulls)
    cache.put(key, mapped)
    return list(mapped)


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


async def _cached_open_pull_counts(
    request: Request,
    client: GitHubClient,
    full_names: Sequence[str],
    semaphore: asyncio.Semaphore,
) -> dict[str, int]:
    """Open pull-request counts per repository, cached per installation.

    Only repositories without a live entry are read, so mounting the picker
    repeatedly costs one read per repository per
    :data:`_PULL_CACHE_TTL_SECONDS` instead of one per mount. A count GitHub
    refused is never cached, so the next request retries it.
    """
    cache: TTLCache[_CacheKey, int] = _app_cache(request, _PULL_COUNTS_CACHE)
    counts: dict[str, int] = {}
    unread: list[str] = []
    for full_name in full_names:
        hit = cache.get(_cache_key(client, full_name))
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
            cache.put(_cache_key(client, full_name), count)
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
