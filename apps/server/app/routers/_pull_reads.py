"""Bounded, cached GitHub reads shared by the repository picker and the inbox.

Both surfaces read the same installation-scoped data — a repository's open pull
requests, one pull request's diff size, a commit's check runs — and both must
survive GitHub refusing one repository without failing the page. This module owns
the pieces that must not drift between them: the concurrency bound, the app-state
TTL caches keyed per installation, and the check-run rollup.

It talks to no GitHub itself. Each caller decides which read it makes, so the
picker pays one read per repository for its counts while the inbox pays for what
a row actually needs (spec v3 §6 §GitHub cost).
"""

from __future__ import annotations

from collections.abc import Sequence

from fastapi import Request

from app.schemas import PullRequestChecks
from slopolis_core.cache import TTLCache
from slopolis_core.github.client import GitHubClient
from slopolis_core.github.models import CheckRun

__all__ = [
    "MAX_CONCURRENT_READS",
    "PULL_CACHE_TTL_SECONDS",
    "CacheKey",
    "app_cache",
    "cache_key",
    "checks_rollup",
]

#: GitHub reads a single request may have in flight at once.
MAX_CONCURRENT_READS = 8

#: How long a read may be served without asking GitHub again. Long enough to
#: absorb a page's worth of mounts, short enough that a review the user just
#: pushed to shows up while they are still looking at the list.
PULL_CACHE_TTL_SECONDS = 30.0

#: A cache entry is scoped to the installation and repository it was read through.
type CacheKey = tuple[int | None, str]

#: Check-run conclusions that count as green.
_PASSING_CONCLUSIONS = frozenset({"success", "neutral", "skipped"})

#: Check-run conclusions that make the rollup fail.
_FAILING_CONCLUSIONS = frozenset(
    {"failure", "timed_out", "cancelled", "action_required", "startup_failure"}
)


def cache_key(client: GitHubClient, full_name: str) -> CacheKey:
    """Scope a cache entry to the installation and repository that produced it.

    A repository re-pointed at another installation can never be served the old
    installation's answer, and a revoke cannot leak into a new install. The test
    seam's fake client carries no installation id, which still keys per
    repository.
    """
    return getattr(client, "installation_id", None), full_name


def app_cache[K, V](request: Request, name: str) -> TTLCache[K, V]:
    """Return the app's cache called ``name``, creating it on first use.

    The cache hangs off the app rather than a module global so test apps do not
    share entries and a deployment can replace it with its own TTL.
    """
    cache: TTLCache[K, V] | None = getattr(request.app.state, name, None)
    if cache is None:
        cache = TTLCache(ttl_seconds=PULL_CACHE_TTL_SECONDS)
        setattr(request.app.state, name, cache)
    return cache


def checks_rollup(runs: Sequence[CheckRun]) -> PullRequestChecks:
    """Roll a head commit's check runs up into the CI state a row shows."""
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
