"""The pull-request inbox (spec v3 §1-§3, §6).

One workspace-wide list of open pull requests, each row joined to what slopolis
knows about its latest review, plus the backlog totals and filter options the
header and filter bar render. It supersedes both the per-repository ``/pulls``
picker endpoint and ``GET /api/dashboard``: the inbox is the app's entry surface,
and the dashboard's aggregate strip is gone with it.

Reads are tiered so a page view does not fan out over the workspace (spec v3 §6
§GitHub cost):

* **Cheap** — one ``list_open_pull_requests`` per connected, enabled repository,
  TTL-cached per installation. It carries the head SHA, which is what staleness
  needs, so filtering, sorting and counting the whole inbox cost no
  per-pull-request read.
* **Expensive** — diff size, the CI rollup, and the commit count between a review
  and the current head: one ``get_pull_request`` plus one ``list_check_runs``
  (and one ``compare_commits`` for a stale review) per row, TTL-cached per pull
  request and bounded by :data:`~app.routers._pull_reads.MAX_CONCURRENT_READS`.
  Only the rows the response needs are hydrated — the page being returned, plus
  the whole filtered set when ``sort=size_desc`` orders on diff size or a
  ``checks`` filter needs every row's CI state to be exact. A pull request GitHub
  refuses degrades to zeros and ``checks: none`` for that row and never fails the
  page.

A review recorded before ``session_targets.reviewed_sha`` existed reports
``reviewed`` with no SHA and no distance: "reviewed" makes no freshness claim, and
the row shows no staleness line. It is still selected by ``review=stale`` and
counted in the backlog, because a review the app cannot compare to the head may
well be behind, and the filter's job is to surface the reviews worth redoing.
Where the SHAs differ the distance comes from GitHub's comparison of the two
commits; a comparison GitHub refuses (a force-pushed head) reports an unknown
distance rather than an invented one.

Access mirrors the dashboard it replaces (spec 10.8 §Access, the same rule
``GET /api/sessions`` applies): a row is visible when the viewer may read its
repository, or when the viewer's own session covers it — they submitted that
review, and pre-flight already verified their access at submit time. The review
join reads only targets the viewer may read, so a row can never surface another
member's session content. One repository GitHub refuses is skipped with a warning,
like the picker's open counts; an inbox whose every repository fails is a 502
rather than an empty list.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import (
    CurrentUserDep,
    DbSessionDep,
    RepoAccessCheckerDep,
    WorkspaceIdDep,
    WorkspaceRepositoriesDep,
)
from app.errors import ApiError
from app.routers._pull_reads import (
    MAX_CONCURRENT_READS,
    app_cache,
    cache_key,
    checks_rollup,
)
from app.routers._pull_request_query import (
    IN_FLIGHT_STATES,
    PullRequestRow,
    clamp_page,
    filter_by_checks,
    filter_options,
    filter_rows,
    needs_distance,
    page_of,
    sort_rows,
    summarize,
    to_item,
    total_pages,
)
from app.routers._session_data import (
    TargetAccess,
    accessible_views,
    finding_counts_for,
    load_sessions,
)
from app.routers._session_query import aware
from app.schemas import (
    PullRequestChecksState,
    PullRequestListParams,
    PullRequestListResponse,
    PullRequestReview,
    PullRequestReviewFilter,
    PullRequestReviewState,
    PullRequestSort,
)
from app.services.github_clients import WorkspaceRepositories
from app.services.repo_access import RepoAccessChecker
from slopolis_core.cache import TTLCache
from slopolis_core.github.client import GitHubClient
from slopolis_core.github.errors import GitHubError
from slopolis_core.github.models import CheckRun, GitHubPullRequest
from slopolis_db.models import Repository, ReviewSession, SessionTarget, SessionTargetRun, User

__all__ = ["router"]

_logger = logging.getLogger(__name__)

router = APIRouter(prefix="/pull-requests", tags=["pull-requests"])

#: ``app.state`` names for the inbox's four caches, so a deployment can swap them.
_PULL_LISTS_CACHE = "pull_lists_cache"
_PULL_DETAILS_CACHE = "pull_details_cache"
_CHECK_RUNS_CACHE = "check_runs_cache"
_COMPARE_CACHE = "compare_cache"

#: A repository-scoped cache key, plus one part per extra read dimension.
type _ListKey = tuple[int | None, str]
type _DetailKey = tuple[int | None, str, int]
type _RunsKey = tuple[int | None, str, str]
type _CompareKey = tuple[int | None, str, str, str]

#: The review state each target status reports (spec v3 §2): everything that is
#: not a finished or in-flight review — failed, cancelled, skipped — is a review
#: that produced nothing and offers a retry.
_STATE_BY_TARGET_STATUS: dict[str, PullRequestReviewState] = {
    "queued": "queued",
    "running": "running",
    "done": "reviewed",
}

#: The steps a running review walks through, used to name the step a live row is
#: on. Moved here from the deleted dashboard, which rendered the same progress.
_STEPS = (
    "Fetching pull request metadata",
    "Snapshotting repository at head",
    "Chunking diff and surrounding context",
    "Scanning changed files for defects",
    "Cross-checking repository conventions",
    "Ranking and drafting findings",
    "Publishing review comments",
)


@dataclass(frozen=True, slots=True)
class _Joined:
    """The session target that decides one pull request's review state."""

    session: ReviewSession
    target: SessionTarget
    findings: int
    run: SessionTargetRun | None


@dataclass(frozen=True, slots=True)
class _Listing:
    """One connected, enabled repository and the open pull requests read for it."""

    repository: Repository
    client: GitHubClient
    pulls: Sequence[GitHubPullRequest]


@router.get("")
async def list_pull_requests(
    request: Request,
    db: DbSessionDep,
    workspace_id: WorkspaceIdDep,
    viewer: CurrentUserDep,
    checker: RepoAccessCheckerDep,
    repositories: WorkspaceRepositoriesDep,
    q: Annotated[str | None, Query()] = None,
    repo: Annotated[str | None, Query()] = None,
    review: Annotated[PullRequestReviewFilter | None, Query()] = None,
    checks: Annotated[PullRequestChecksState | None, Query()] = None,
    drafts: Annotated[bool, Query()] = False,
    page: Annotated[int, Query()] = 1,
    pageSize: Annotated[int, Query()] = 25,
    sort: Annotated[PullRequestSort, Query()] = "updated_desc",
) -> PullRequestListResponse:
    """Return a filtered, paged page of the workspace's open pull requests.

    The rows, the summary and the filter options all describe the set the viewer
    may read: the page is a slice of the filtered set, the totals count that set
    rather than the page, and the filter options count every row rather than the
    active filters (spec v3 §3).
    """
    params = PullRequestListParams(
        q=q,
        repo=repo,
        review=review,
        checks=checks,
        drafts=drafts,
        page=page,
        page_size=pageSize,
        sort=sort,
    )
    candidates = await _candidate_rows(
        request,
        db,
        workspace_id=workspace_id,
        viewer=viewer,
        checker=checker,
        repositories=repositories,
    )
    clients = {
        listing.repository.full_name: listing.client for listing in candidates.listings
    }

    filtered = filter_rows(candidates.rows, params)
    if params.checks:
        # GitHub's listing carries no CI state, so the only honest way to filter
        # on it is to read it for every row the filter has to decide about.
        await _hydrate(request, filtered, clients)
        filtered = filter_by_checks(filtered, params.checks)
    if params.sort == "size_desc":
        # Same reason, for diff size: the order is decided by a number that only
        # the expensive tier has.
        await _hydrate(request, filtered, clients)

    ordered = sort_rows(filtered, params.sort)
    page_number, page_size = clamp_page(params.page, params.page_size)
    items = page_of(ordered, page_number, page_size)
    await _hydrate(request, items, clients)

    return PullRequestListResponse(
        items=[to_item(row) for row in items],
        page=page_number,
        page_size=page_size,
        total=len(filtered),
        total_pages=total_pages(len(filtered), page_size),
        summary=summarize(filtered),
        filter_options=filter_options(candidates.rows, repositories=candidates.repositories),
        generated_at=dt.datetime.now(dt.UTC),
    )


@dataclass(frozen=True, slots=True)
class _Candidates:
    """What one request's cheap tier and access rule produced.

    ``rows`` are the rows the viewer may read; ``repositories`` are the names the
    filter bar may offer.
    """

    listings: list[_Listing]
    rows: list[PullRequestRow]
    repositories: list[str]


async def _candidate_rows(
    request: Request,
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    viewer: User,
    checker: RepoAccessChecker,
    repositories: WorkspaceRepositories,
) -> _Candidates:
    """Read every served repository's open pull requests and join their reviews.

    The cheap tier: one listing per connected, enabled repository, cached per
    installation. A repository the App cannot read is skipped with a warning —
    the picker's open-count behaviour — but a workspace whose every repository
    failed answers 502 rather than an empty inbox that reads as "nothing open".
    """
    listings, attempted, failed = await _read_listings(request, repositories)
    if attempted and failed == attempted:
        raise ApiError(
            502,
            "pull_requests_unavailable",
            "Could not load the pull-request inbox.",
            detail=f"None of this workspace's {attempted} repositories could be read.",
        )

    sessions = await load_sessions(db, workspace_id=workspace_id)
    views = await accessible_views(db, sessions, viewer=viewer, checker=checker)
    joined = await _review_join(db, sessions, views)

    # The same rule the dashboard applied (spec 10.8 §Access): a repository the
    # viewer cannot read shows nothing, except a pull request the viewer's own
    # session covers — pre-flight verified their access when they submitted it.
    verdicts = await checker.can_read_many(
        viewer, [listing.repository.full_name for listing in listings]
    )
    mine = {
        (target.repository_id, target.number)
        for session in sessions
        if session.triggered_by_user_id == viewer.id
        for target in session.targets
    }
    rows = [
        PullRequestRow(
            pull=pull,
            review=_review_for(joined.get((listing.repository.id, pull.number)), pull),
        )
        for listing in listings
        for pull in listing.pulls
        if verdicts.get(listing.repository.full_name) is True
        or (listing.repository.id, pull.number) in mine
    ]
    # The bar offers every repository the viewer may read — including one with
    # nothing open, so "nothing open" is sayable — plus any repository their own
    # review put a row of on screen.
    offered = {name for name, verdict in verdicts.items() if verdict is True}
    offered.update(row.pull.repo_full_name for row in rows)
    return _Candidates(listings=listings, rows=rows, repositories=sorted(offered))


async def _read_listings(
    request: Request, repositories: WorkspaceRepositories
) -> tuple[list[_Listing], int, int]:
    """Read the open pull requests of every connected, enabled repository.

    Returns the listings, how many repositories were attempted, and how many of
    those GitHub refused. An installation whose client cannot be minted fails its
    repositories the same way a refused read does.
    """
    listings: list[_Listing] = []
    attempted = 0
    failed = 0
    pending: list[tuple[Repository, GitHubClient]] = []
    for installation, group in await repositories.by_installation():
        served = [row for row in group if row.enabled and row.connected]
        if not served:
            continue
        attempted += len(served)
        client = await repositories.client_for_installation(installation)
        if client is None:
            failed += len(served)
            _logger.warning(
                "GitHub client unavailable for installation %s; skipping %s",
                installation.installation_id,
                ", ".join(row.full_name for row in served),
            )
            continue
        pending.extend((row, client) for row in served)

    semaphore = asyncio.Semaphore(MAX_CONCURRENT_READS)

    async def read(row: Repository, client: GitHubClient) -> _Listing | None:
        async with semaphore:
            pulls = await _cached_open_pulls(request, client, row.full_name)
        return None if pulls is None else _Listing(repository=row, client=client, pulls=pulls)

    for result in await asyncio.gather(*(read(row, client) for row, client in pending)):
        if result is None:
            failed += 1
        else:
            listings.append(result)
    return listings, attempted, failed


async def _cached_open_pulls(
    request: Request, client: GitHubClient, full_name: str
) -> list[GitHubPullRequest] | None:
    """The repository's open pull requests, served from the cache.

    ``None`` means GitHub refused the repository: the failure is logged and never
    cached, so the next request retries it rather than freezing a bad read.
    """
    cache: TTLCache[_ListKey, list[GitHubPullRequest]] = app_cache(
        request, _PULL_LISTS_CACHE
    )
    key = cache_key(client, full_name)
    hit = cache.get(key)
    if hit is not None:
        # Hand out a copy: the cached list must survive whatever the caller does.
        return list(hit)
    try:
        pulls = list(await client.list_open_pull_requests(full_name))
    except GitHubError as exc:
        _logger.warning("open pull requests unavailable for %s: %s", full_name, exc)
        return None
    cache.put(key, pulls)
    return list(pulls)


async def _review_join(
    db: AsyncSession,
    sessions: Sequence[ReviewSession],
    views: Mapping[uuid.UUID, TargetAccess],
) -> dict[tuple[uuid.UUID, int], _Joined]:
    """Return the session target that decides each pull request's review state.

    Keyed by ``(repository, number)`` and built only from targets the viewer may
    read, so a row can never carry another member's session. The latest target
    wins, except that one still in flight outranks an older completed attempt
    (spec v3 §2): a review that is running shows as running until it finishes.
    """
    matches: dict[tuple[uuid.UUID, int], list[tuple[ReviewSession, SessionTarget]]] = {}
    for session in sessions:
        for target in views[session.id].targets:
            matches.setdefault((target.repository_id, target.number), []).append(
                (session, target)
            )
    if not matches:
        return {}

    latest = {key: _latest(pairs) for key, pairs in matches.items()}
    target_ids = [target.id for _session, target in latest.values()]
    counts = await finding_counts_for(db, target_ids)
    runs = await _latest_runs(db, target_ids)
    return {
        key: _Joined(
            session=session,
            target=target,
            findings=counts.get(target.id, 0),
            run=runs.get(target.id),
        )
        for key, (session, target) in latest.items()
    }


def _latest(
    pairs: Sequence[tuple[ReviewSession, SessionTarget]],
) -> tuple[ReviewSession, SessionTarget]:
    """Return the pair that decides a pull request's review state.

    In-flight attempts first — a queued or running review outranks a completed
    one for the same pull request — then the most recent submission.
    """
    in_flight = [pair for pair in pairs if pair[1].status in IN_FLIGHT_STATES]
    pool = in_flight or list(pairs)
    return max(pool, key=lambda pair: (aware(pair[0].created_at), aware(pair[1].created_at)))


async def _latest_runs(
    db: AsyncSession, target_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, SessionTargetRun]:
    """Return each target's latest attempt, keyed by target id."""
    if not target_ids:
        return {}
    rows = (
        await db.scalars(
            select(SessionTargetRun)
            .where(SessionTargetRun.target_id.in_(target_ids))
            .order_by(SessionTargetRun.attempt)
        )
    ).all()
    return {row.target_id: row for row in rows}


def _review_for(joined: _Joined | None, pull: GitHubPullRequest) -> PullRequestReview:
    """Build one row's review column from the target that decided it.

    ``never`` when no target has ever covered the pull request. An in-flight
    review reports progress and step and makes no freshness claim; a finished one
    reports the SHA it covered and how far behind it is — or no SHA at all, for a
    review recorded before the column existed; a failed, cancelled or skipped
    attempt reports the session to retry from.
    """
    if joined is None:
        return PullRequestReview(state="never")

    session = joined.session
    target = joined.target
    state = _STATE_BY_TARGET_STATUS.get(target.status, "failed")
    reviewed_sha = target.reviewed_sha
    if state in IN_FLIGHT_STATES:
        progress = _progress(session, target)
        return PullRequestReview(
            state=state,
            session_id=str(session.id),
            reviewed_sha=reviewed_sha,
            commits_since_review=0,
            findings_count=joined.findings,
            progress=progress,
            step=_step_for(progress),
            reviewed_at=None,
        )
    if state == "failed":
        return PullRequestReview(
            state="failed",
            session_id=str(session.id),
            reviewed_sha=reviewed_sha,
            commits_since_review=0,
            findings_count=joined.findings,
            reviewed_at=_finished_at(session, joined.run),
        )
    # Reviewed: a distance of zero only when the review is known to cover the
    # head. A review recorded before `reviewed_sha` existed had no commit to
    # compare, so it reports no distance at all (null) rather than claiming to be
    # current, exactly like a review of an earlier commit whose distance GitHub
    # has not counted yet.
    measured = reviewed_sha is not None and reviewed_sha == pull.head_sha
    return PullRequestReview(
        state="reviewed",
        session_id=str(session.id),
        reviewed_sha=reviewed_sha,
        commits_since_review=0 if measured else None,
        findings_count=joined.findings,
        reviewed_at=_finished_at(session, joined.run),
    )


def _finished_at(
    session: ReviewSession, run: SessionTargetRun | None
) -> dt.datetime | None:
    """When a review attempt finished: its latest run, else its session."""
    finished = run.finished_at if run is not None else None
    value = finished if finished is not None else session.finished_at
    return aware(value) if value is not None else None


def _progress(session: ReviewSession, target: SessionTarget) -> int:
    """Derive a stable 0-100 progress value for a live target."""
    done = sum(1 for item in session.targets if item.status in ("done", "failed"))
    total = max(1, len(session.targets))
    base = (done / total) * 100
    if target.status == "running":
        return min(99, int(base) + 40)
    return min(20, int(base))


def _step_for(progress: int) -> str:
    """Name the step a live review has reached from its progress."""
    return _STEPS[min(len(_STEPS) - 1, (progress * len(_STEPS)) // 100)]


async def _hydrate(
    request: Request,
    rows: Sequence[PullRequestRow],
    clients: Mapping[str, GitHubClient],
) -> None:
    """Fill diff size, the CI rollup, and the review distance for these rows.

    Concurrently, but never more than
    :data:`~app.routers._pull_reads.MAX_CONCURRENT_READS` at a time, and only for
    rows no earlier tier has already read. Each read is cached per pull request,
    so paging back and forth inside the TTL costs GitHub nothing.
    """
    pending = [row for row in rows if not row.hydrated]
    if not pending:
        return
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_READS)

    async def one(row: PullRequestRow) -> None:
        client = clients.get(row.pull.repo_full_name)
        if client is None:
            return
        async with semaphore:
            await _hydrate_row(request, client, row)

    await asyncio.gather(*(one(row) for row in pending))


async def _hydrate_row(
    request: Request, client: GitHubClient, row: PullRequestRow
) -> None:
    """Read one row's expensive fields, degrading to zeros if GitHub refuses it."""
    pull = row.pull
    full_name = pull.repo_full_name
    try:
        detail = await _cached_detail(request, client, full_name, pull.number)
        runs = await _cached_check_runs(request, client, full_name, pull.head_sha)
    except GitHubError as exc:
        # A pull request GitHub refuses — deleted, transferred, or a repository the
        # installation lost — is one row with nothing known about it, never a
        # failed page.
        _logger.warning("pull request %s#%s unavailable: %s", full_name, pull.number, exc)
        row.hydrated = True
        return

    row.changed_files = detail.changed_files
    row.additions = detail.additions
    row.deletions = detail.deletions
    row.checks = checks_rollup(runs)
    row.hydrated = True
    if needs_distance(row):
        row.review = row.review.model_copy(
            update={
                "commits_since_review": await _cached_commits_between(
                    request, client, full_name, row.review.reviewed_sha, pull.head_sha
                )
            }
        )


async def _cached_detail(
    request: Request, client: GitHubClient, full_name: str, number: int
) -> GitHubPullRequest:
    """One pull request's detail read, served from the cache."""
    cache: TTLCache[_DetailKey, GitHubPullRequest] = app_cache(request, _PULL_DETAILS_CACHE)
    key = (*cache_key(client, full_name), number)
    hit = cache.get(key)
    if hit is not None:
        return hit
    detail = await client.get_pull_request(full_name, number)
    cache.put(key, detail)
    return detail


async def _cached_check_runs(
    request: Request, client: GitHubClient, full_name: str, ref: str
) -> list[CheckRun]:
    """A head commit's check runs, served from the cache."""
    cache: TTLCache[_RunsKey, list[CheckRun]] = app_cache(request, _CHECK_RUNS_CACHE)
    key = (*cache_key(client, full_name), ref)
    hit = cache.get(key)
    if hit is not None:
        return list(hit)
    runs = list(await client.list_check_runs(full_name, ref))
    cache.put(key, runs)
    return list(runs)


async def _cached_commits_between(
    request: Request,
    client: GitHubClient,
    full_name: str,
    reviewed_sha: str | None,
    head_sha: str,
) -> int | None:
    """How many commits the head moved past a review, or ``None`` when unknown.

    GitHub refuses to compare a pair that no longer shares a history — a
    force-pushed head is the ordinary case. That is an unknown distance, not a
    current review, so the row keeps a ``null`` count rather than a fabricated
    one, and never fails the page over it.
    """
    if reviewed_sha is None:
        return None
    cache: TTLCache[_CompareKey, int] = app_cache(request, _COMPARE_CACHE)
    key = (*cache_key(client, full_name), reviewed_sha, head_sha)
    hit = cache.get(key)
    if hit is not None:
        return hit
    try:
        count = await client.compare_commits(full_name, reviewed_sha, head_sha)
    except GitHubError as exc:
        _logger.warning(
            "could not count commits between %s and %s in %s: %s",
            reviewed_sha,
            head_sha,
            full_name,
            exc,
        )
        return None
    cache.put(key, count)
    return count
