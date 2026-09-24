"""Pure filter, sort, page, and mapping helpers for the pull-request inbox.

Kept separate from the router so the query semantics are unit-testable in
isolation and the router stays a thin HTTP shell — the ``_session_query`` idiom.
Every filter dimension of :class:`PullRequestListParams` is applied here, over
the rows the viewer may read: never over the page, and never over a repository
the viewer cannot see.

A row is cheap by construction: it carries what one GitHub listing per repository
answered plus the review join, so filtering, sorting and counting the whole inbox
costs no per-pull-request read. The one filter that cannot be answered from that
is ``checks`` — GitHub reports no CI state in its listing — so the caller
hydrates the cheap-filtered set before asking this module for it (see
``pull_requests.py``).
"""

from __future__ import annotations

import datetime as dt
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field

from app.schemas import (
    FilterOption,
    PullRequestChecks,
    PullRequestFilterOptions,
    PullRequestListItem,
    PullRequestListParams,
    PullRequestReview,
    PullRequestSummary,
    RepositoryRef,
    UserRef,
)
from slopolis_core.github.models import GitHubPullRequest

__all__ = [
    "IN_FLIGHT_STATES",
    "PullRequestRow",
    "clamp_page",
    "filter_by_checks",
    "filter_options",
    "filter_rows",
    "is_stale",
    "matches_query",
    "needs_distance",
    "page_of",
    "sort_rows",
    "summarize",
    "to_item",
    "total_pages",
]

#: The review states that mean a review is still on its way.
IN_FLIGHT_STATES = ("queued", "running")

#: The review filter's vocabulary, in the order the filter bar shows it. ``stale``
#: refines ``reviewed`` rather than replacing it, so a stale row counts in both.
_REVIEW_FILTERS: tuple[tuple[str, str], ...] = (
    ("never", "Never reviewed"),
    ("queued", "Queued"),
    ("running", "Running"),
    ("reviewed", "Reviewed"),
    ("stale", "Stale"),
    ("failed", "Failed"),
)

#: The CI states a row can carry, in the order the filter bar shows them.
_CHECK_FILTERS: tuple[tuple[str, str], ...] = (
    ("passing", "Passing"),
    ("failing", "Failing"),
    ("pending", "Pending"),
    ("none", "None"),
)

_PAGE_SIZE_MAX = 100


def _no_checks() -> PullRequestChecks:
    """What a row shows before the expensive tier has read anything for it.

    GitHub's listing carries no CI state and no diff size, so an unhydrated row
    reports the same ``none``/zeros a pull request GitHub refuses degrades to.
    """
    return PullRequestChecks(state="none", total=0, passing=0)


@dataclass(slots=True)
class PullRequestRow:
    """One candidate inbox row: a listing entry, its review join, and what the
    expensive tier has filled in so far."""

    pull: GitHubPullRequest
    review: PullRequestReview
    changed_files: int = 0
    additions: int = 0
    deletions: int = 0
    checks: PullRequestChecks = field(default_factory=_no_checks)
    #: Whether the expensive tier has read this row (diff size and CI state).
    hydrated: bool = False


def is_stale(row: PullRequestRow) -> bool:
    """Whether the row's review is worth redoing because it may be behind.

    True for a reviewed row whose head has moved on, and equally for one whose
    review recorded no commit at all: the app cannot compare such a review to the
    head, so it may well be behind, and the filter's job is to surface the reviews
    worth redoing. A review at the current head is the only one that is provably
    current, so it is the only one this says no to.

    Decided from the SHAs and the count rather than from the count alone, which
    the expensive tier has not produced yet for most rows: a review of an earlier
    commit is behind whatever the count turns out to be, and the head SHA is in
    the cheap tier precisely so this filter can be exact without a
    per-pull-request read.
    """
    review = row.review
    if review.state != "reviewed":
        return False
    count = review.commits_since_review
    return review.reviewed_sha is None or count is None or count > 0


def needs_distance(row: PullRequestRow) -> bool:
    """Whether GitHub has to be asked how far behind the row's review is.

    Only a review that recorded a commit *different* from the head has a distance
    to count: one with no recorded commit makes no freshness claim to measure, and
    one at the head is current by construction. This is what the expensive tier
    compares, which is why it is narrower than :func:`is_stale`.
    """
    sha = row.review.reviewed_sha
    return row.review.state == "reviewed" and sha is not None and sha != row.pull.head_sha


def filter_rows(
    rows: Sequence[PullRequestRow], params: PullRequestListParams
) -> list[PullRequestRow]:
    """Return only the rows satisfying every filter the cheap tier can answer.

    ``checks`` is deliberately not among them: GitHub's listing carries no CI
    state, so that filter is applied by :func:`filter_by_checks` once the rows it
    has to decide about have been read.
    """
    return [row for row in rows if _matches(row, params)]


def filter_by_checks(
    rows: Sequence[PullRequestRow], state: str
) -> list[PullRequestRow]:
    """Keep only the rows whose CI rollup is ``state``.

    Separate from :func:`filter_rows` because a row's CI state is only known once
    the expensive tier has read it: the caller hydrates the cheap-filtered set
    first, so this filter is exact rather than a guess over unread rows.
    """
    return [row for row in rows if row.checks.state == state]


def _matches(row: PullRequestRow, params: PullRequestListParams) -> bool:
    """Return whether a row satisfies every active filter the cheap tier knows."""
    return not (
        (row.pull.draft and not params.drafts)
        or (params.q and not matches_query(row, params.q))
        or (params.repo and row.pull.repo_full_name != params.repo)
        or (params.review and not matches_review(row, params.review))
    )


def matches_query(row: PullRequestRow, query: str) -> bool:
    """Free-text match across a row's title, repository, number, and author.

    Case-insensitive, over the same haystacks the mock serves: the title, the
    repository full name, ``#number``, ``owner/name#number``, and the author's
    handle (spec v3 §3).
    """
    pull = row.pull
    needle = query.lower()
    haystacks = (
        pull.title,
        pull.repo_full_name,
        f"#{pull.number}",
        f"{pull.repo_full_name}#{pull.number}",
        pull.author_login,
    )
    return any(needle in value.lower() for value in haystacks)


def matches_review(row: PullRequestRow, review: str) -> bool:
    """Return whether a row matches one review filter value.

    ``stale`` selects the reviews behind the head, while ``reviewed`` selects the
    state — so a stale row matches both (spec v3 §2/§3).
    """
    if review == "stale":
        return is_stale(row)
    return row.review.state == review


def sort_rows(rows: Sequence[PullRequestRow], sort: str) -> list[PullRequestRow]:
    """Return a new list in the requested order (newest activity first by default).

    ``staleness_desc`` orders on what is cheaply known — reviewed work first, then
    attempts that need a retry or are in flight, then work nobody has looked at —
    and breaks ties on how far behind a review is where that has been counted. The
    commit counts of rows the page did not hydrate are unknown, so they rank with
    an unknown distance of one rather than being read for the ordering.
    """
    if sort == "size_desc":
        return sorted(rows, key=_size_key, reverse=True)
    if sort == "staleness_desc":
        return sorted(rows, key=_staleness_key, reverse=True)
    if sort == "created_desc":
        return sorted(rows, key=_created_key, reverse=True)
    return sorted(rows, key=_updated, reverse=True)


def clamp_page(page: int, page_size: int) -> tuple[int, int]:
    """Clamp a requested page and page size to what the inbox serves.

    A page number left over from a filter change is answered with the last page
    that exists rather than refused, so the UI never shows an error for a page the
    user did not ask for (spec v3 §3).
    """
    return max(1, page), min(_PAGE_SIZE_MAX, max(1, page_size))


def page_of(
    rows: Sequence[PullRequestRow], page: int, page_size: int
) -> list[PullRequestRow]:
    """Return the slice of ``rows`` the requested page covers."""
    start = (page - 1) * page_size
    return list(rows[start : start + page_size])


def total_pages(total: int, page_size: int) -> int:
    """Return how many pages ``total`` rows fill, or 0 when there are none."""
    return 0 if total == 0 else (total + page_size - 1) // page_size


def summarize(rows: Sequence[PullRequestRow]) -> PullRequestSummary:
    """Count the backlog the inbox header states (spec v3 §3).

    ``needsReview`` is work nobody has reviewed plus reviews that are behind the
    head; ``running`` is the reviews on their way.
    """
    stale = sum(1 for row in rows if is_stale(row))
    return PullRequestSummary(
        total=len(rows),
        needs_review=stale + sum(1 for row in rows if row.review.state == "never"),
        stale=stale,
        running=sum(1 for row in rows if row.review.state in IN_FLIGHT_STATES),
    )


def filter_options(
    rows: Sequence[PullRequestRow], *, repositories: Sequence[str]
) -> PullRequestFilterOptions:
    """Count every filter dimension over the whole row set.

    Counted over every row the viewer may read rather than over the page or the
    active filters, so the bar can tell "nothing open" from "nothing matches"
    (spec v3 §3). ``repositories`` is every repository the inbox could serve, so
    one with nothing open is still selectable; ``checks`` carries no count because
    the CI state is read per page, and a count over rows nobody hydrated would be
    a guess rather than a count.
    """
    per_repo: Counter[str] = Counter(row.pull.repo_full_name for row in rows)
    per_review: Counter[str] = Counter(row.review.state for row in rows)
    stale = sum(1 for row in rows if is_stale(row))
    return PullRequestFilterOptions(
        repositories=[
            FilterOption(value=full_name, label=full_name, hint=str(per_repo[full_name]))
            for full_name in repositories
        ],
        reviews=[
            FilterOption(
                value=value,
                label=label,
                # ``stale`` counts the reviewed rows behind the head, on top of
                # the ``reviewed`` count they already appear in.
                hint=str(stale if value == "stale" else per_review[value]),
            )
            for value, label in _REVIEW_FILTERS
        ],
        checks=[
            FilterOption(value=value, label=label) for value, label in _CHECK_FILTERS
        ],
    )


def to_item(row: PullRequestRow) -> PullRequestListItem:
    """Map a hydrated row onto the wire shape the UI renders."""
    pull = row.pull
    return PullRequestListItem(
        id=_pull_id(pull),
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
        head_branch=pull.head_branch,
        head_sha=pull.head_sha,
        updated_at=pull.updated_at,
        draft=pull.draft,
        # GitHub's pull-request object carries no comment count on the reads the
        # inbox makes, and the mock serves none either.
        comments=0,
        changed_files=row.changed_files,
        additions=row.additions,
        deletions=row.deletions,
        checks=row.checks,
        review=row.review,
    )


def _pull_id(pull: GitHubPullRequest) -> str:
    """The stable row id for one pull request."""
    return f"pr_{pull.repo_full_name.replace('/', '_')}_{pull.number}"


def _size(row: PullRequestRow) -> int:
    """The row's diff size, as the ``size_desc`` order reads it."""
    return row.additions + row.deletions


def _size_key(row: PullRequestRow) -> tuple[int, float]:
    """Diff size first, then recency, as a sortable key."""
    return _size(row), _updated(row)


def _staleness_key(row: PullRequestRow) -> tuple[int, int, float]:
    """Staleness rank, then distance behind, then recency, as a sortable key."""
    state = row.review.state
    rank = 0 if state == "reviewed" else 2 if state == "never" else 1
    return -rank, _commits_behind(row), _updated(row)


def _commits_behind(row: PullRequestRow) -> int:
    """How far behind a review is, counting an unknown distance as one commit.

    A review the app cannot measure — one that recorded no commit, or whose
    comparison GitHub refused — is stale without a known distance, so it ranks as
    one commit behind rather than as current.
    """
    count = row.review.commits_since_review
    if count:
        return count
    return 1 if is_stale(row) else 0


def _created_key(row: PullRequestRow) -> tuple[float, float]:
    """Creation first, then recency, as a sortable key."""
    return _timestamp(row.pull.created_at), _updated(row)


def _updated(row: PullRequestRow) -> float:
    """The row's last-activity timestamp, as epoch seconds."""
    return _timestamp(row.pull.updated_at)


def _timestamp(value: str) -> float:
    """Parse an ISO-8601 timestamp, reading an absent one as the epoch."""
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    return (parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=dt.UTC)).timestamp()
