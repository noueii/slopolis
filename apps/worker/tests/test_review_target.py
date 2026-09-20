"""Behavior tests for the review_target job (spec 10.5-10.9).

Every test runs against in-memory SQLite with fake LLM/GitHub clients; no
network, no Redis. Given/When/Then structure with fakes asserting on the exact
values the job produces.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from decimal import Decimal

import pytest
from arq import Retry
from sqlalchemy import select
from worker.config import WorkerConfig
from worker.deps import SessionFactory
from worker.jobs.loading import TargetJob, load_target
from worker.jobs.review_target import review_target
from worker.jobs.slots import SLOT_WAIT_FOREVER, SlotGate
from worker_fakes import (
    CATALOG_MODEL,
    HEAD_BRANCH,
    HEAD_SHA,
    REPO_FULL_NAME,
    FakeLlm,
    FakeReader,
    FakeRedis,
)
from worker_seed import Harness, seed_and_build

from slopolis_core.domain import TargetStatus
from slopolis_core.github.errors import GitHubError
from slopolis_core.llm.models import ChatMessage, CompletionResult
from slopolis_db.models import (
    Finding,
    ReviewSession,
    SessionTarget,
    SessionTargetRun,
    UsageRecord,
)

_FINDINGS_JSON = (
    '{"findings": ['
    '{"path": "src/a.py", "line": 3, "severity": "error", "category": "correctness", '
    '"message": "boom", "suggestion": "fix it", "confidence": 0.9}, '
    '{"path": "src/a.py", "line": null, "severity": "warning", "category": "style", '
    '"message": "nit", "suggestion": null, "confidence": 0.5}'
    "]}"
)

_INVALID_CONFIG = "review:\n  severity_threshold: [not, a, severity]\n"

#: Caps being enforced, with a wait no test runs out by accident.
_GATE_CONFIG = WorkerConfig(
    WORKER_MAX_TRIES=4,
    WORKER_RETRY_BACKOFF_S=1,
    WORKER_SLOT_WAIT_S=5.0,
    WORKER_SLOT_POLL_S=0.01,
)

#: The same, with a wait short enough for a test to deliberately run it out.
_SHORT_WAIT_CONFIG = WorkerConfig(
    WORKER_MAX_TRIES=4,
    WORKER_RETRY_BACKOFF_S=1,
    WORKER_SLOT_WAIT_S=0.05,
    WORKER_SLOT_POLL_S=0.01,
)

_SECOND_PR = 43


class _GatedLlm(FakeLlm):
    """FakeLlm that parks every review inside its single model call.

    A review makes exactly one model call, and it happens while the target holds
    its slots, so how many calls overlap is how a test sees concurrency.
    """

    def __init__(self, responses: list[str]) -> None:
        super().__init__(responses)
        self.active = 0
        self.peak = 0
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        model: str,
        max_tokens: int | None = None,
        temperature: float = 0.0,
    ) -> CompletionResult:
        self.active += 1
        self.peak = max(self.peak, self.active)
        self.entered.set()
        try:
            await self.release.wait()
            return await super().complete(
                messages, model=model, max_tokens=max_tokens, temperature=temperature
            )
        finally:
            self.active -= 1


async def _until(predicate: Callable[[], bool], *, timeout: float = 5.0) -> None:
    """Await until ``predicate`` holds, so a test never races a poll interval."""
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.005)


async def _run(h: Harness, target_id: uuid.UUID | None = None) -> None:
    await review_target(
        h.ctx,
        str(h.seed.session_id),
        str(target_id if target_id is not None else h.seed.target_id),
    )


def _gate(h: Harness) -> SlotGate:
    """Return the harness's slot gate (a harness with caps always has one)."""
    gate = h.ctx.get("slots")
    assert gate is not None
    return gate


async def _loaded_job(h: Harness, target_id: uuid.UUID | None = None) -> TargetJob:
    """Load the job graph for one target, the way the gate reads it."""
    async with h.session_factory() as db:
        load = await load_target(
            db,
            str(h.seed.session_id),
            str(target_id if target_id is not None else h.seed.target_id),
        )
        assert load.job is not None
        return load.job


async def _add_target(h: Harness, number: int) -> uuid.UUID:
    """Queue one more PR of the seeded repository in the seeded session."""
    async with h.session_factory() as db:
        first = await db.get(SessionTarget, h.seed.target_id)
        assert first is not None
        target = SessionTarget(
            session_id=h.seed.session_id,
            repository_id=first.repository_id,
            number=number,
            title=f"Add search {number}",
            url=f"https://github.com/{REPO_FULL_NAME}/pull/{number}",
            head_branch="main",
            status=TargetStatus.QUEUED,
        )
        db.add(target)
        await db.flush()
        return target.id


async def _target(h: Harness, target_id: uuid.UUID | None = None) -> SessionTarget:
    async with h.session_factory() as db:
        loaded = await db.get(
            SessionTarget, target_id if target_id is not None else h.seed.target_id
        )
        assert loaded is not None
        return loaded


async def _session(h: Harness) -> ReviewSession:
    async with h.session_factory() as db:
        loaded = await db.get(ReviewSession, h.seed.session_id)
        assert loaded is not None
        return loaded


async def _findings(h: Harness) -> list[Finding]:
    async with h.session_factory() as db:
        rows = await db.execute(select(Finding).where(Finding.target_id == h.seed.target_id))
        return list(rows.scalars().all())


async def _runs(h: Harness) -> list[SessionTargetRun]:
    async with h.session_factory() as db:
        rows = await db.execute(
            select(SessionTargetRun).where(SessionTargetRun.target_id == h.seed.target_id)
        )
        return list(rows.scalars().all())


async def _usage(h: Harness) -> list[UsageRecord]:
    async with h.session_factory() as db:
        rows = await db.execute(
            select(UsageRecord).where(UsageRecord.target_id == h.seed.target_id)
        )
        return list(rows.scalars().all())


async def test_happy_path_persists_and_publishes(session_factory: SessionFactory) -> None:
    # Given a ready target, valid findings JSON, and no repo config file
    h = await seed_and_build(session_factory, llm=FakeLlm([_FINDINGS_JSON]))

    # When the job runs
    await _run(h)

    # Then both findings persist, only the line-mapped one is posted
    findings = {row.line: row for row in await _findings(h)}
    assert len(findings) == 2
    assert findings[3].severity == "error"
    assert findings[3].posted is True
    assert findings[3].github_comment_id == 201
    assert findings[None].posted is False
    assert findings[None].github_comment_id is None

    # ... the summary carries the session link, status, and usage line
    assert len(h.seed.publisher.summaries) == 1
    _, _, summary, existing_id = h.seed.publisher.summaries[0]
    assert existing_id is None
    assert f"/sessions/{h.seed.session_id}" in summary
    assert "done" in summary
    assert "15 tokens" in summary
    assert "$0.0020" in summary
    assert "boom" in summary and "nit" in summary

    # ... exactly one inline comment, anchored to the true head commit
    assert len(h.seed.publisher.inlines) == 1
    _, _, comments, commit_id = h.seed.publisher.inlines[0]
    assert commit_id == HEAD_SHA
    assert len(comments) == 1
    assert comments[0].path == "src/a.py"
    assert comments[0].line == 3
    assert "suggestion" in comments[0].body

    # ... the check run fails on the error finding
    assert len(h.seed.publisher.checks) == 1
    _, head_sha, conclusion, _, _ = h.seed.publisher.checks[0]
    assert head_sha == HEAD_SHA
    assert conclusion == "failure"

    # ... usage, target totals, and target head_branch are recorded
    usage = await _usage(h)
    assert len(usage) == 1
    assert usage[0].total_tokens == 15
    assert usage[0].model_id == CATALOG_MODEL
    assert usage[0].session_id == h.seed.session_id
    target = await _target(h)
    assert target.status == "done"
    assert target.tokens == 15
    assert target.cost_usd == Decimal("0.002")
    assert target.duration_ms is not None
    assert target.head_branch == HEAD_BRANCH

    # ... the attempt finished and the session is done
    runs = await _runs(h)
    assert len(runs) == 1
    assert runs[0].status == "done"
    assert runs[0].attempt == 1
    assert (await _session(h)).status == "done"

    # ... and the catalog-assigned model was used (no hardcoded name)
    assert h.seed.llm.models == [CATALOG_MODEL]


async def test_parse_failure_publishes_summary_only(session_factory: SessionFactory) -> None:
    # Given the model returns unparseable output twice
    h = await seed_and_build(session_factory, llm=FakeLlm(["not json", "still not json"]))

    # When the job runs
    await _run(h)

    # Then no findings and no inline comments, but the summary notes the failure
    assert await _findings(h) == []
    assert h.seed.publisher.inlines == []
    assert len(h.seed.publisher.summaries) == 1
    assert "could not be parsed" in h.seed.publisher.summaries[0][2]

    # ... the check run succeeds and the target still completes
    assert h.seed.publisher.checks[0][2] == "success"
    assert (await _target(h)).status == "done"
    assert (await _session(h)).status == "done"


async def test_invalid_repo_config_fails_target_without_publishing(
    session_factory: SessionFactory,
) -> None:
    # Given `.codereview.yml` that fails to parse
    h = await seed_and_build(
        session_factory,
        reader=FakeReader(config_text=_INVALID_CONFIG),
        llm=FakeLlm([_FINDINGS_JSON]),
    )

    # When the job runs
    await _run(h)

    # Then the target fails without retrying and nothing is published
    assert (await _target(h)).status == "failed"
    assert h.seed.publisher.summaries == []
    assert h.seed.publisher.inlines == []
    assert h.seed.publisher.checks == []
    runs = await _runs(h)
    assert len(runs) == 1
    assert runs[0].status == "failed"
    assert runs[0].error is not None
    assert "PermanentTargetError" in runs[0].error
    assert (await _session(h)).status == "failed"


async def test_cancelled_session_marks_target_cancelled(
    session_factory: SessionFactory,
) -> None:
    # Given a session cancelled before the target ran
    h = await seed_and_build(session_factory, llm=FakeLlm([_FINDINGS_JSON]))
    async with h.session_factory() as db:
        session = await db.get(ReviewSession, h.seed.session_id)
        assert session is not None
        session.status = "cancelled"

    # When the job runs
    await _run(h)

    # Then the target is cancelled and nothing else happens
    assert (await _target(h)).status == "cancelled"
    assert await _runs(h) == []
    assert h.seed.publisher.summaries == []
    assert (await _session(h)).status == "cancelled"


async def test_transient_failure_retries_then_exhausts(
    session_factory: SessionFactory,
) -> None:
    # Given a GitHub read that raises on attempt 1 of 4
    h = await seed_and_build(
        session_factory,
        reader=FakeReader(fail_with=GitHubError("kaboom")),
        config=WorkerConfig(WORKER_MAX_TRIES=4, WORKER_RETRY_BACKOFF_S=1),
        job_try=1,
    )

    # When the job runs it defers a retry
    with pytest.raises(Retry):
        await _run(h)

    # Then the attempt is failed but the target stays running for the next try
    runs = await _runs(h)
    assert len(runs) == 1
    assert runs[0].status == "failed"
    assert runs[0].error is not None and "GitHubError" in runs[0].error
    assert (await _target(h)).status == "running"
    assert (await _session(h)).status == "running"


async def test_final_attempt_failure_marks_target_and_session_failed(
    session_factory: SessionFactory,
) -> None:
    # Given the last allowed attempt also fails
    h = await seed_and_build(
        session_factory,
        reader=FakeReader(fail_with=GitHubError("kaboom")),
        config=WorkerConfig(WORKER_MAX_TRIES=4, WORKER_RETRY_BACKOFF_S=1),
        job_try=4,
    )

    # When the job runs it stops retrying
    await _run(h)

    # Then the target and session are failed with the error recorded
    target = await _target(h)
    assert target.status == "failed"
    runs = await _runs(h)
    assert runs[-1].status == "failed"
    assert runs[-1].error is not None and "GitHubError" in runs[-1].error
    assert (await _session(h)).status == "failed"


async def test_repo_cap_keeps_one_target_running_at_a_time(
    session_factory: SessionFactory,
) -> None:
    # Given one repository capped at a single running target, and two queued PRs
    llm = _GatedLlm([_FINDINGS_JSON, _FINDINGS_JSON])
    redis = FakeRedis()
    h = await seed_and_build(
        session_factory, llm=llm, config=_GATE_CONFIG, redis=redis, repo_cap=1
    )
    second = await _add_target(h, _SECOND_PR)

    # When both jobs run at once
    first_run = asyncio.create_task(_run(h))
    await asyncio.wait_for(llm.entered.wait(), timeout=5)
    held_calls = redis.calls
    second_run = asyncio.create_task(_run(h, second))
    await _until(lambda: redis.calls > held_calls)

    # Then the second target is waiting on its slot rather than reviewing
    assert llm.active == 1
    assert (await _target(h, second)).status == "queued"

    # ... and once the first target finishes, the second one runs too
    llm.release.set()
    await asyncio.wait_for(asyncio.gather(first_run, second_run), timeout=10)
    assert llm.peak == 1
    assert (await _target(h)).status == "done"
    assert (await _target(h, second)).status == "done"
    assert len(h.seed.publisher.summaries) == 2
    assert (await _session(h)).status == "done"


async def test_target_without_a_slot_defers_instead_of_failing(
    session_factory: SessionFactory,
) -> None:
    # Given a repository whose only slot a target is already holding
    redis = FakeRedis()
    h = await seed_and_build(
        session_factory,
        llm=FakeLlm([_FINDINGS_JSON]),
        config=_SHORT_WAIT_CONFIG,
        redis=redis,
        repo_cap=1,
    )
    gate = _gate(h)
    job = await _loaded_job(h)

    async with gate.hold(job, wait_s=SLOT_WAIT_FOREVER):
        # When a queued target of that repository runs and the wait runs out
        with pytest.raises(Retry) as deferral:
            await _run(h)

    # Then it is deferred back onto the queue, with nothing written about it
    assert deferral.value.defer_score == int(_SHORT_WAIT_CONFIG.slot_defer_s * 1000)
    assert (await _target(h)).status == "queued"
    assert await _runs(h) == []
    assert h.seed.publisher.summaries == []

    # ... and with the slot free, that same target runs to completion
    await _run(h)
    assert (await _target(h)).status == "done"
    assert (await _session(h)).status == "done"


async def test_a_target_cancelled_while_it_waits_is_not_reviewed(
    session_factory: SessionFactory,
) -> None:
    # Given a target waiting for a slot its repository is already using
    redis = FakeRedis()
    h = await seed_and_build(
        session_factory,
        llm=FakeLlm([_FINDINGS_JSON]),
        config=_GATE_CONFIG,
        redis=redis,
        repo_cap=1,
    )
    gate = _gate(h)

    async with gate.hold(await _loaded_job(h), wait_s=SLOT_WAIT_FOREVER):
        held_calls = redis.calls
        pending = asyncio.create_task(_run(h))
        await _until(lambda: redis.calls > held_calls)

        # When its session is cancelled before it ever gets in
        async with h.session_factory() as db:
            session = await db.get(ReviewSession, h.seed.session_id)
            assert session is not None
            session.status = "cancelled"

    await asyncio.wait_for(pending, timeout=10)

    # Then the target is cancelled instead of being reviewed late
    assert (await _target(h)).status == "cancelled"
    assert await _runs(h) == []
    assert h.seed.publisher.summaries == []


async def test_the_last_try_waits_for_a_slot_instead_of_deferring(
    session_factory: SessionFactory,
) -> None:
    # Given the last permitted try of a target whose repository slot is taken
    redis = FakeRedis()
    h = await seed_and_build(
        session_factory,
        llm=FakeLlm([_FINDINGS_JSON]),
        config=_SHORT_WAIT_CONFIG,
        job_try=_SHORT_WAIT_CONFIG.max_tries,
        redis=redis,
        repo_cap=1,
    )
    gate = _gate(h)
    job = await _loaded_job(h)

    async with gate.hold(job, wait_s=SLOT_WAIT_FOREVER):
        # When it runs, it outlasts the wait a deferring try would have given up after
        held_calls = redis.calls
        pending = asyncio.create_task(_run(h))
        await _until(lambda: redis.calls > held_calls)
        await asyncio.sleep(_SHORT_WAIT_CONFIG.slot_wait_s * 4)
        assert not pending.done()

    # Then it starts as soon as the slot frees instead of deferring itself into a
    # queue that has no tries left for it
    await asyncio.wait_for(pending, timeout=10)
    assert (await _target(h)).status == "done"
