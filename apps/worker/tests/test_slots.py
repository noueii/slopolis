"""Behavior tests for the per-repo/installation slot gate (spec 10.5).

The gate is driven against a real target graph loaded from in-memory SQLite and
an in-memory Redis stand-in: which slots a target holds, who has to wait for
them, and what a holder that never cleans up leaves behind. No network, no Redis.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Callable

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from worker.config import WorkerConfig
from worker.deps import SessionFactory
from worker.jobs.loading import TargetJob, load_target
from worker.jobs.slots import (
    SLOT_WAIT_FOREVER,
    SlotGate,
    SlotUnavailable,
    WorkspaceLimits,
    slot_key,
)
from worker_fakes import FakeRedis
from worker_seed import Harness, seed_and_build

from slopolis_core.domain import TargetStatus
from slopolis_db.models import Repository, SessionTarget

_FAST = WorkerConfig(
    WORKER_MAX_TRIES=4,
    WORKER_RETRY_BACKOFF_S=1,
    WORKER_SLOT_WAIT_S=5.0,
    WORKER_SLOT_POLL_S=0.01,
)

_SIBLING_REPO = "acme/sibling"


class _Boom(RuntimeError):
    """A failure raised inside a hold, standing in for a dead attempt."""


async def _until(predicate: Callable[[], bool], *, timeout: float = 5.0) -> None:
    """Await until ``predicate`` holds, so a test never races a poll interval."""
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.005)


def _gate(h: Harness) -> SlotGate:
    """Return the harness's slot gate (a harness with limits always has one)."""
    gate = h.ctx.get("slots")
    assert gate is not None
    return gate


async def _job(h: Harness, target_id: uuid.UUID | None = None) -> TargetJob:
    """Load the real target graph the gate reads its limits and keys from."""
    target = target_id if target_id is not None else h.seed.target_id
    async with h.session_factory() as db:
        load = await load_target(db, str(h.seed.session_id), str(target))
        assert load.job is not None
        return load.job


async def _hold(gate: SlotGate, job: TargetJob, *, wait_s: float) -> bool:
    """Take one hold and report whether the gate had to wait for it."""
    async with gate.hold(job, wait_s=wait_s) as waited:
        return waited


async def _sibling_job(h: Harness) -> TargetJob:
    """Load a ready job for a second repository of the seeded target's installation."""
    async with h.session_factory() as db:
        sibling = await _add_sibling_repo(db, h)
        target = SessionTarget(
            session_id=h.seed.session_id,
            repository_id=sibling,
            number=99,
            title="Add search",
            url=f"https://github.com/{_SIBLING_REPO}/pull/99",
            head_branch="main",
            status=TargetStatus.QUEUED,
        )
        db.add(target)
        await db.flush()
        load = await load_target(db, str(h.seed.session_id), str(target.id))
        assert load.job is not None
        return load.job


async def _add_sibling_repo(db: AsyncSession, h: Harness) -> uuid.UUID:
    """Connect a second repository to the installation the seeded target uses."""
    first = await db.get(SessionTarget, h.seed.target_id)
    assert first is not None
    repository = await db.get(Repository, first.repository_id)
    assert repository is not None
    sibling = Repository(
        workspace_id=repository.workspace_id,
        installation_id=repository.installation_id,
        github_id=repository.github_id + 1,
        full_name=_SIBLING_REPO,
        private=True,
        default_branch="main",
    )
    db.add(sibling)
    await db.flush()
    return sibling.id


async def test_unlimited_caps_never_touch_redis(session_factory: SessionFactory) -> None:
    # Given a workspace that never set a cap
    redis = FakeRedis()
    h = await seed_and_build(session_factory, redis=redis, config=_FAST)

    # When a target holds its slots
    async with _gate(h).hold(await _job(h), wait_s=SLOT_WAIT_FOREVER) as waited:
        # Then it never waited, and Redis was never asked anything at all
        assert waited is False

    assert redis.calls == 0
    assert redis.sets == {}


async def test_a_second_holder_waits_for_the_first_to_release(
    session_factory: SessionFactory,
) -> None:
    # Given a repository capped at one running target
    redis = FakeRedis()
    h = await seed_and_build(session_factory, redis=redis, repo_cap=1, config=_FAST)
    gate = _gate(h)
    job = await _job(h)

    # When a second target asks for the slot the first one holds
    async with gate.hold(job, wait_s=SLOT_WAIT_FOREVER):
        held_calls = redis.calls
        waiter = asyncio.create_task(_hold(gate, job, wait_s=SLOT_WAIT_FOREVER))
        await _until(lambda: redis.calls > held_calls)

        # Then it keeps waiting instead of taking the slot
        assert not waiter.done()

    # ... and it gets the slot, and reports that it had to wait for it
    assert await asyncio.wait_for(waiter, timeout=1.0) is True
    assert redis.sets[slot_key("repo", job.repository.id)] == {}


async def test_a_failed_hold_releases_its_slot(session_factory: SessionFactory) -> None:
    # Given a target holding the repository's only slot
    redis = FakeRedis()
    h = await seed_and_build(session_factory, redis=redis, repo_cap=1, config=_FAST)
    gate = _gate(h)
    job = await _job(h)

    # When the attempt inside the hold blows up
    with pytest.raises(_Boom):
        async with gate.hold(job, wait_s=SLOT_WAIT_FOREVER):
            raise _Boom

    # Then the slot is free again
    assert redis.members(slot_key("repo", job.repository.id)) == {}


async def test_a_released_slot_is_reusable(session_factory: SessionFactory) -> None:
    # Given a target that held and released the repository's only slot
    redis = FakeRedis()
    h = await seed_and_build(session_factory, redis=redis, repo_cap=1, config=_FAST)
    gate = _gate(h)
    job = await _job(h)
    assert await _hold(gate, job, wait_s=SLOT_WAIT_FOREVER) is False

    # When the next target asks for it
    # Then it takes it without waiting, and the hold it left behind is gone
    assert await _hold(gate, job, wait_s=SLOT_WAIT_FOREVER) is False
    assert redis.members(slot_key("repo", job.repository.id)) == {}


async def test_a_live_holder_is_never_pruned(session_factory: SessionFactory) -> None:
    # Given a holder whose job has been running for its whole job timeout
    redis = FakeRedis()
    h = await seed_and_build(session_factory, redis=redis, repo_cap=1, config=_FAST)
    job = await _job(h)
    now = [time.time()]
    gate = SlotGate(redis, limits=WorkspaceLimits(), config=_FAST, clock=lambda: now[0])

    async with gate.hold(job, wait_s=SLOT_WAIT_FOREVER):
        now[0] += _FAST.job_timeout_s

        # When a second target asks for the slot
        # Then the live hold still counts, so it is refused rather than doubled up
        with pytest.raises(SlotUnavailable):
            async with gate.hold(job, wait_s=0):
                pytest.fail("the live holder's slot was handed out twice")


async def test_a_dead_holders_slot_is_reclaimed(session_factory: SessionFactory) -> None:
    # Given a worker that died holding the only slot, leaving its lease behind
    redis = FakeRedis()
    h = await seed_and_build(session_factory, redis=redis, repo_cap=1, config=_FAST)
    gate = _gate(h)
    job = await _job(h)
    await redis.zadd(slot_key("repo", job.repository.id), {"dead-worker": 0.0})

    # When a live target asks for that slot
    # Then it gets it at once: nothing waits out a worker that is gone
    assert await _hold(gate, job, wait_s=0) is False


async def test_the_installation_cap_bounds_across_repositories(
    session_factory: SessionFactory,
) -> None:
    # Given one installation capped at a single running target, with two repositories
    redis = FakeRedis()
    h = await seed_and_build(session_factory, redis=redis, installation_cap=1, config=_FAST)
    gate = _gate(h)
    first = await _job(h)
    sibling = await _sibling_job(h)
    assert sibling.installation.id == first.installation.id
    assert sibling.repository.id != first.repository.id

    # When a target of the first repository holds its slot
    # Then a target of the other repository is refused even though its own repo is free
    async with gate.hold(first, wait_s=SLOT_WAIT_FOREVER):
        with pytest.raises(SlotUnavailable):
            async with gate.hold(sibling, wait_s=0):
                pytest.fail("two targets of one installation ran at once")

    # ... and it gets the slot once the first one releases it
    assert await _hold(gate, sibling, wait_s=SLOT_WAIT_FOREVER) is False
