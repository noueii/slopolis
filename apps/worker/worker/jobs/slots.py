"""Per-repository and per-installation run slots (spec 10.5).

An ARQ queue is one sorted set with no per-key concurrency, so two targets of the
same repository are indistinguishable to it. The bound therefore lives where the
jobs run: a target takes one slot per configured dimension before it executes and
gives them back when it finishes, and every worker process shares the bound
through Redis.

Slots are leases, not locks. A hold is a member of a sorted set scored with the
wall-clock time it was taken, and the gate prunes members older than the lease
before it counts one. A worker that dies while holding a slot therefore stops
counting after at most one lease, with nothing left to unwind. The lease is the
job timeout plus a margin, so a job that is still running is never pruned out
from underneath itself.

Waiting and deferring. A target that finds no free slot must not fail, so a hold
waits for a caller-supplied budget and then raises :class:`SlotUnavailable`; the
job turns that into an ARQ deferral (:func:`worker.jobs.review_target._slot_wait`
sizes the budget). The wait is the primary mechanism: it absorbs ordinary
contention inside the job, letting the review start the instant a slot frees, at
the price of one parked worker process and the database session it is holding.
Deferring is the escape hatch for a repository that stays busy for minutes — it
hands the worker process back to the queue, where other repositories can use it,
and re-enters this job later. Both are bounded: the wait is capped well below
``WORKER_JOB_TIMEOUT_S`` so a job that does get its slot still has time to do the
work, and ARQ counts every deferral as a try, so a job only defers while
``WORKER_MAX_TRIES`` has room left. Neither the wait nor the deferral writes
anything about the target: the queue decides a target's fate, never the gate.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
import uuid
from collections.abc import AsyncGenerator, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Protocol

from worker.config import WorkerConfig
from worker.jobs.loading import TargetJob

__all__ = [
    "SLOT_WAIT_FOREVER",
    "LimitsSource",
    "SlotCounter",
    "SlotGate",
    "SlotUnavailable",
    "TargetLimits",
    "WorkspaceLimits",
    "slot_key",
]

_LOG = logging.getLogger("worker.slots")

#: Key namespace shared by every worker process counting the same bound.
_KEY_PREFIX = "slopolis:slots"

#: Wait budget that never runs out, for a caller with no queue to defer to.
SLOT_WAIT_FOREVER = math.inf

#: Lease margin over ``WORKER_JOB_TIMEOUT_S``. A live job cannot outlast its own
#: timeout, so this only has to cover the release round trip and clock skew.
_LEASE_MARGIN_S = 60.0


@dataclass(frozen=True, slots=True)
class TargetLimits:
    """How many targets of one repository, and of one installation, may run at once."""

    per_repo: int | None
    per_installation: int | None


class LimitsSource(Protocol):
    """Where a target's caps come from (spec 10.2; unset means unlimited)."""

    def limits_for(self, job: TargetJob) -> TargetLimits: ...


class WorkspaceLimits:
    """The caps as set on the workspace the target belongs to."""

    def limits_for(self, job: TargetJob) -> TargetLimits:
        """Read the two queue caps off the target's workspace row."""
        return TargetLimits(
            per_repo=job.workspace.max_targets_per_repo,
            per_installation=job.workspace.max_targets_per_installation,
        )


class SlotCounter(Protocol):
    """The sorted-set subset of the async Redis client the gate needs.

    Declared separately from ``redis.asyncio.Redis`` because redis-py types its
    commands as a sync/async union; the ARQ pool satisfies this at runtime.
    """

    async def zadd(self, name: str, mapping: Mapping[str, float]) -> int: ...

    async def zcard(self, name: str) -> int: ...

    async def zrem(self, name: str, *values: str) -> int: ...

    async def zremrangebyscore(self, name: str, min: float, max: float) -> int: ...

    async def pexpire(self, name: str, time_ms: int) -> bool: ...


class SlotUnavailable(RuntimeError):
    """A hold ran out of wait before it owned every slot it needed."""


def slot_key(dimension: str, ident: object) -> str:
    """Return the Redis key counting one dimension's running targets."""
    return f"{_KEY_PREFIX}:{dimension}:{ident}"


@dataclass(frozen=True, slots=True)
class _Dimension:
    """One capped dimension: what it counts and how many of it may run at once."""

    key: str
    limit: int


class SlotGate:
    """Bound how many targets of one repository (and installation) run at once.

    One gate per worker process, shared by every job it runs and by every other
    worker through the ``counter``; the caps are read per target, so an admin
    change applies to the next target that starts.
    """

    def __init__(
        self,
        counter: SlotCounter,
        *,
        limits: LimitsSource,
        config: WorkerConfig,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._counter = counter
        self._limits = limits
        self._poll_s = config.slot_poll_s
        self._lease_ms = int((config.job_timeout_s + _LEASE_MARGIN_S) * 1000)
        self._clock = clock

    @asynccontextmanager
    async def hold(self, job: TargetJob, *, wait_s: float) -> AsyncGenerator[bool]:
        """Hold this target's slots for the length of the block.

        Waits up to ``wait_s`` seconds for a free slot per capped dimension and
        raises :class:`SlotUnavailable` without holding anything if it runs out.
        Yields whether it had to wait at all, so a caller that waited can
        re-read whatever may have changed underneath it. The slots are released
        on every exit from the block, successful or not.
        """
        dimensions = self._dimensions(job)
        if not dimensions:
            # Unset means unlimited (spec 10.2): not one Redis round trip.
            yield False
            return
        token = uuid.uuid4().hex
        # Each hold retries at its own pace, derived from its token: the gate is
        # add-then-count, so a batch of workers that retried in lockstep would
        # all withdraw together and starve. Staggering them drains the batch in a
        # few rounds instead (spec 10.5 wants batches, not a stampede).
        poll_s = self._poll_s * (0.5 + (int(token[:8], 16) % 1000) / 1000)
        waited = False
        deadline = self._clock() + wait_s
        while not await self._take(dimensions, token=token):
            if self._clock() >= deadline:
                raise SlotUnavailable(
                    f"no free slot for {job.repo_full_name} within {wait_s:g}s"
                )
            waited = True
            await asyncio.sleep(poll_s)
        try:
            yield waited
        finally:
            await self._withdraw(dimensions, token=token)

    def _dimensions(self, job: TargetJob) -> tuple[_Dimension, ...]:
        """Return this target's capped dimensions, unlimited ones left out."""
        limits = self._limits.limits_for(job)
        dimensions: list[_Dimension] = []
        if limits.per_repo is not None:
            dimensions.append(
                _Dimension(
                    key=slot_key("repo", job.repository.id), limit=limits.per_repo
                )
            )
        if limits.per_installation is not None:
            dimensions.append(
                _Dimension(
                    key=slot_key("installation", job.installation.id),
                    limit=limits.per_installation,
                )
            )
        return tuple(dimensions)

    async def _take(self, dimensions: Sequence[_Dimension], *, token: str) -> bool:
        """Take every slot, or take none of them.

        Each worker adds itself before it counts, and withdraws if the set is
        over its limit. That cannot overshoot: every worker that keeps checked a
        set that already held every other live keeper's entry, so more than
        ``limit`` keepers is impossible — and an established holder is never
        displaced, because only the worker that just counted itself backs off.
        Two workers that add within the same instant can both back off one poll
        interval; neither of them runs, so the bound holds either way.
        """
        now_ms = int(self._clock() * 1000)
        stale_before = now_ms - self._lease_ms
        taken: list[_Dimension] = []
        for dimension in dimensions:
            _ = await self._counter.zremrangebyscore(
                dimension.key, float("-inf"), stale_before
            )
            _ = await self._counter.zadd(dimension.key, {token: now_ms})
            held = await self._counter.zcard(dimension.key)
            if held > dimension.limit:
                await self._withdraw([*taken, dimension], token=token)
                return False
            taken.append(dimension)
            # Outlive the members: they stop being counted by lease, and a key
            # that vanishes with live members would hand out a slot twice.
            _ = await self._counter.pexpire(dimension.key, self._lease_ms * 2)
        return True

    async def _withdraw(self, dimensions: Sequence[_Dimension], *, token: str) -> None:
        """Give up every slot this token holds, in any dimension."""
        for dimension in dimensions:
            _ = await self._counter.zrem(dimension.key, token)
