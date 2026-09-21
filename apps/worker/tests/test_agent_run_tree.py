"""Run-tree tests for the review target job (spec v2 §7-§8, §15).

What a review finds and posts is `test_review_target.py`'s subject; these tests
are the tree's: the main -> pr -> sub nodes the job writes, their per-run event
logs, the runtime-enforced 1:1 between a target and its PR run, and what
cancel/recompute do to nodes. In-memory SQLite, fake clients, no network.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from arq import Retry
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from worker.deps import SessionFactory
from worker.jobs.agent_runs import DatabaseRunStore
from worker.jobs.review_target import review_target
from worker_fakes import (
    CATALOG_MODEL,
    PR_NUMBER,
    REPO_FULL_NAME,
    FakeLlm,
    FakePublisher,
    FakeReader,
)
from worker_seed import Harness, Seed, build_harness, seed_and_build

from slopolis_core.context import PrContext
from slopolis_core.domain import SessionStatus, TargetStatus
from slopolis_core.github.errors import GitHubAuthError, GitHubError
from slopolis_core.harness import HarnessLevel
from slopolis_db.models import AgentEventRow, AgentRun, ReviewSession, SessionTarget

_FINDINGS_JSON = (
    '{"findings": ['
    '{"path": "src/a.py", "line": 3, "severity": "error", "category": "correctness", '
    '"message": "boom", "suggestion": "fix it", "confidence": 0.9}, '
    '{"path": "src/a.py", "line": null, "severity": "warning", "category": "style", '
    '"message": "nit", "suggestion": null, "confidence": 0.5}'
    "]}"
)

_SECOND_PR = 43


class _FailFirstContextReader(FakeReader):
    """Fails the harness's PR-context read on its first call only.

    That read happens inside the reviewer run, so the failure lands after the
    target's PR run and its first reviewer run exist.
    """

    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    async def get_pr_context(self, repo_full_name: str, number: int) -> PrContext:
        self.calls += 1
        if self.calls == 1:
            raise GitHubError("context read failed")
        return await super().get_pr_context(repo_full_name, number)


def _harness_for(
    h: Harness,
    *,
    target_id: uuid.UUID,
    reader: FakeReader | None = None,
    publisher: FakePublisher | None = None,
    llm: FakeLlm | None = None,
    job_try: int = 1,
) -> Harness:
    """Build a job context over the seeded session but a specific target."""
    seed = Seed(
        workspace_id=h.seed.workspace_id,
        session_id=h.seed.session_id,
        target_id=target_id,
        reader=reader if reader is not None else FakeReader(),
        publisher=publisher if publisher is not None else FakePublisher(),
        llm=llm if llm is not None else FakeLlm([_FINDINGS_JSON]),
    )
    return build_harness(session_factory=h.session_factory, seed=seed, job_try=job_try)


async def _add_target(h: Harness, number: int) -> uuid.UUID:
    """Add a second PR target to the seeded session, as the submit path would."""
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


async def _run(h: Harness, target_id: uuid.UUID) -> None:
    await review_target(h.ctx, str(h.seed.session_id), str(target_id))


async def _runs(h: Harness, *, level: str | None = None) -> list[AgentRun]:
    """The seeded session's run rows, oldest first, optionally one level."""
    async with h.session_factory() as db:
        stmt = select(AgentRun).where(AgentRun.session_id == h.seed.session_id)
        if level is not None:
            stmt = stmt.where(AgentRun.level == level)
        rows = await db.execute(stmt.order_by(AgentRun.created_at, AgentRun.level))
        return list(rows.scalars().all())


async def _events(h: Harness, run_id: uuid.UUID) -> list[AgentEventRow]:
    """One run's events in sequence order."""
    async with h.session_factory() as db:
        rows = await db.execute(
            select(AgentEventRow)
            .where(AgentEventRow.run_id == run_id)
            .order_by(AgentEventRow.seq)
        )
        return list(rows.scalars().all())


async def _session(h: Harness) -> ReviewSession:
    async with h.session_factory() as db:
        loaded = await db.get(ReviewSession, h.seed.session_id)
        assert loaded is not None
        return loaded


async def _target(h: Harness, target_id: uuid.UUID) -> SessionTarget:
    async with h.session_factory() as db:
        loaded = await db.get(SessionTarget, target_id)
        assert loaded is not None
        return loaded


def _types(events: list[AgentEventRow]) -> list[str]:
    return [event.type for event in events]


def _seqs(events: list[AgentEventRow]) -> list[int]:
    return [event.seq for event in events]


async def test_a_target_review_writes_the_whole_run_tree(
    session_factory: SessionFactory,
) -> None:
    # Given a ready target and a model that returns two findings
    h = await seed_and_build(session_factory, llm=FakeLlm([_FINDINGS_JSON]))

    # When the job runs
    await _run(h, h.seed.target_id)

    # Then the session has exactly one main, one pr, and one sub run
    runs = await _runs(h)
    assert len(runs) == 3
    nodes = {run.level: run for run in runs}
    main, pr, sub = nodes["main"], nodes["pr"], nodes["sub"]

    # ... wired main -> pr -> sub, with the PR run tied to the target
    assert main.role == "orchestrator.main"
    assert main.parent_run_id is None
    assert main.target_id is None
    assert pr.role == "orchestrator.pr"
    assert pr.parent_run_id == main.id
    assert pr.target_id == h.seed.target_id
    assert sub.role == "reviewer"
    assert sub.parent_run_id == pr.id
    assert sub.target_id == h.seed.target_id

    # ... the reviewer ran on the workspace-assigned model for this PR
    assert sub.model_id == CATALOG_MODEL
    assert f"#{PR_NUMBER}" in sub.objective

    # ... every node ended done, including the session's main node
    assert (main.status, pr.status, sub.status) == ("done", "done", "done")
    assert main.ended_at is not None
    assert main.tokens == 15

    # ... the main node was spawned by the job (no submit path in this test) and
    #     closed by the session recompute
    main_events = await _events(h, main.id)
    assert _types(main_events) == ["agent.spawned", "agent.started", "agent.completed"]
    assert main_events[0].payload["level"] == "main"
    assert main_events[0].payload["parent_run_id"] is None

    # ... the PR node logged its delegated turn, the summary, and both findings
    pr_events = await _events(h, pr.id)
    assert _types(pr_events) == [
        "agent.spawned",
        "agent.started",
        "agent.step",
        "agent.message",
        "agent.finding",
        "agent.finding",
        "agent.completed",
    ]
    assert pr_events[0].payload["target_id"] == str(h.seed.target_id)
    assert pr_events[2].payload["tool_call_count"] == 1

    # ... the sub node is the reviewer's own run, ending in exactly one terminal
    #     event whose payload matches its row
    sub_events = await _events(h, sub.id)
    assert _types(sub_events) == [
        "agent.spawned",
        "agent.started",
        "agent.step",
        "agent.message",
        "agent.finding",
        "agent.finding",
        "agent.completed",
    ]
    assert sub_events[2].payload["model_id"] == CATALOG_MODEL
    assert sub_events[-1].payload["status"] == sub.status == "done"
    assert sub_events[-1].payload["tokens_used"] == 15
    assert sub_events[-1].payload["finding_count"] == 2

    # ... and every node's log is contiguous from seq 1
    for run in runs:
        events = await _events(h, run.id)
        assert _seqs(events) == list(range(1, len(events) + 1))


async def test_a_retry_reuses_the_pr_run_and_adds_one_sub_run(
    session_factory: SessionFactory,
) -> None:
    # Given a first attempt whose PR-context read fails inside the reviewer run
    h = await seed_and_build(
        session_factory,
        reader=_FailFirstContextReader(),
        llm=FakeLlm([_FINDINGS_JSON]),
    )

    with pytest.raises(Retry):
        await _run(h, h.seed.target_id)

    # ... which left one failed PR run and one failed reviewer run in the tree
    prs = await _runs(h, level="pr")
    subs = await _runs(h, level="sub")
    assert len(prs) == 1
    assert len(subs) == 1
    assert prs[0].status == "failed"
    assert subs[0].status == "failed"
    assert subs[0].parent_run_id == prs[0].id
    pr_id = prs[0].id

    # When the retry runs
    h.ctx["job_try"] = 2
    await _run(h, h.seed.target_id)

    # Then the target still has exactly one PR run, the same row as before
    prs = await _runs(h, level="pr")
    assert len(prs) == 1
    assert prs[0].id == pr_id
    assert prs[0].status == "done"

    # ... and the retry added a second reviewer run under it, while the first
    #     attempt's reviewer run stayed as it ended
    subs = await _runs(h, level="sub")
    assert len(subs) == 2
    assert {sub.parent_run_id for sub in subs} == {pr_id}
    assert sorted(sub.status for sub in subs) == ["done", "failed"]
    failed_sub = next(sub for sub in subs if sub.status == "failed")
    failed_events = await _events(h, failed_sub.id)
    assert _types(failed_events)[-1] == "agent.failed"
    assert "GitHubError" in str(failed_events[-1].payload["error"])

    # ... with the PR node's log continued rather than restarted: one spawn, one
    #     start and one terminal event per attempt, contiguous across both
    events = await _events(h, pr_id)
    assert _types(events).count("agent.spawned") == 1
    assert _types(events).count("agent.started") == 2
    assert _types(events).count("agent.failed") == 1
    assert _types(events).count("agent.completed") == 1
    assert _seqs(events) == list(range(1, len(events) + 1))


async def test_the_partial_index_rejects_a_second_pr_run_for_one_target(
    session_factory: SessionFactory,
) -> None:
    # Given a target whose review already ran
    h = await seed_and_build(session_factory, llm=FakeLlm([_FINDINGS_JSON]))
    await _run(h, h.seed.target_id)
    prs = await _runs(h, level="pr")
    assert len(prs) == 1

    async with h.session_factory() as db:
        # When a second PR run is written for the same (session, target)
        db.add(
            AgentRun(
                session_id=h.seed.session_id,
                target_id=h.seed.target_id,
                parent_run_id=prs[0].parent_run_id,
                level="pr",
                role="orchestrator.pr",
                model_id=None,
                objective="a second PR orchestrator",
                status="running",
                started_at=dt.datetime.now(dt.UTC),
            )
        )

        # Then the runtime-enforced 1:1 rejects it
        with pytest.raises(IntegrityError):
            await db.flush()
        await db.rollback()

        # ... while extra sub runs for the same target are still allowed
        db.add(
            AgentRun(
                session_id=h.seed.session_id,
                target_id=h.seed.target_id,
                parent_run_id=prs[0].id,
                level="sub",
                role="reviewer",
                model_id=CATALOG_MODEL,
                objective="a second reviewer",
                status="running",
                started_at=dt.datetime.now(dt.UTC),
            )
        )
        await db.flush()
        await db.commit()


async def test_recompute_finishes_main_with_the_session_totals(
    session_factory: SessionFactory,
) -> None:
    # Given a session with two targets, both of which will be reviewed
    h = await seed_and_build(session_factory, llm=FakeLlm([_FINDINGS_JSON]))
    second_id = await _add_target(h, _SECOND_PR)
    second = _harness_for(h, target_id=second_id)

    # When both target jobs run
    await _run(h, h.seed.target_id)
    await _run(second, second_id)

    # Then each target has its own PR run and reviewer run
    prs = await _runs(h, level="pr")
    assert {run.target_id for run in prs} == {h.seed.target_id, second_id}
    assert len(await _runs(h, level="sub")) == 2

    # ... and the session's main run carries the sum of both targets
    mains = await _runs(h, level="main")
    assert len(mains) == 1
    assert mains[0].status == "done"
    assert mains[0].tokens == 30
    assert float(mains[0].cost_usd) == 0.004
    assert mains[0].ended_at is not None
    assert (await _session(h)).status == "done"


async def test_a_cancelled_session_cancels_the_whole_target_tree(
    session_factory: SessionFactory,
) -> None:
    # Given a session cancelled while one target's attempt was still in flight,
    # which is how an interrupted attempt leaves its runs behind
    h = await seed_and_build(session_factory, llm=FakeLlm([]))
    async with h.session_factory() as db:
        session = await db.get(ReviewSession, h.seed.session_id)
        target = await db.get(SessionTarget, h.seed.target_id)
        assert session is not None and target is not None
        store = DatabaseRunStore(db)
        main = await store.find_or_create_main_run(session)
        pr = await store.find_or_create_pr_run(session, target, main.id)
        await store.create_run(
            session_id=session.id,
            target_id=target.id,
            parent_run_id=pr.id,
            level=HarnessLevel.SUB,
            role="reviewer",
            model_id=CATALOG_MODEL,
            objective=f"Review PR #{PR_NUMBER}",
        )
        session.status = "cancelled"

    # When the target job runs
    await _run(h, h.seed.target_id)

    # Then the target and all three runs are cancelled, and nothing was reviewed
    assert (await _target(h, h.seed.target_id)).status == "cancelled"
    assert (await _session(h)).status == "cancelled"
    runs = await _runs(h)
    assert {run.level for run in runs} == {"main", "pr", "sub"}
    for run in runs:
        assert run.status == "cancelled"
        assert run.ended_at is not None
        events = _types(await _events(h, run.id))
        assert events[-1] == "agent.cancelled"
        assert events.count("agent.cancelled") == 1
    assert h.seed.publisher.summaries == []
    assert h.seed.llm.models == []


async def test_one_failing_target_leaves_the_other_runs_intact(
    session_factory: SessionFactory,
) -> None:
    # Given two targets in one session, one of which cannot be read
    h = await seed_and_build(session_factory, llm=FakeLlm([_FINDINGS_JSON]))
    second_id = await _add_target(h, _SECOND_PR)
    failing = _harness_for(
        h,
        target_id=h.seed.target_id,
        reader=FakeReader(fail_with=GitHubError("kaboom")),
        llm=FakeLlm([]),
        job_try=4,
    )
    second = _harness_for(h, target_id=second_id)

    # When the failing target exhausts its attempts, then the other one succeeds
    await _run(failing, h.seed.target_id)
    assert (await _target(h, h.seed.target_id)).status == "failed"
    still_running = await _runs(h, level="pr")
    assert [run.status for run in still_running] == ["failed"]
    assert (await _session(h)).status == "running"

    await _run(second, second_id)

    # Then the good target's nodes are untouched by the other one's failure
    runs = await _runs(h)
    by_target = {run.target_id: run for run in runs if run.level == "pr"}
    assert by_target[h.seed.target_id].status == "failed"
    assert by_target[second_id].status == "done"
    assert _types(await _events(h, by_target[h.seed.target_id].id)) == [
        "agent.spawned",
        "agent.started",
        "agent.failed",
    ]
    subs = await _runs(h, level="sub")
    assert len(subs) == 1
    assert subs[0].target_id == second_id
    assert subs[0].status == "done"
    assert subs[0].parent_run_id == by_target[second_id].id

    # ... and the session, with one failed target, closes its main run as failed
    assert (await _session(h)).status == "failed"
    mains = await _runs(h, level="main")
    assert len(mains) == 1
    assert mains[0].status == "failed"
    assert _types(await _events(h, mains[0].id)) == [
        "agent.spawned",
        "agent.started",
        "agent.failed",
    ]


async def _requeue(h: Harness, target_id: uuid.UUID) -> None:
    """Put a target and its session back to ``queued``, as the retry API does.

    A terminal target is skipped by the job's own guard, so a retry that did not
    reset the statuses first would enqueue work the worker would ignore.
    """
    async with h.session_factory() as db:
        target = await db.get(SessionTarget, target_id)
        session = await db.get(ReviewSession, h.seed.session_id)
        assert target is not None and session is not None
        target.status = TargetStatus.QUEUED
        session.status = SessionStatus.QUEUED
        session.finished_at = None
        await db.commit()


async def test_a_retried_attempt_reopens_the_nodes_it_reuses(
    session_factory: SessionFactory,
) -> None:
    """A reused node must not keep the failed attempt's status (spec §15).

    The tree is the run's visible state, and a terminal root is never finished
    again (`finish_main_run`) — so a retry that left the main run `failed` would
    let the session complete underneath a failed root.
    """
    # Given a target whose publish GitHub refused, which closed every node
    h = await seed_and_build(
        session_factory, llm=FakeLlm([_FINDINGS_JSON, _FINDINGS_JSON])
    )
    refused = _harness_for(
        h,
        target_id=h.seed.target_id,
        publisher=FakePublisher(fail_with=GitHubAuthError("publish refused (HTTP 403)")),
        llm=FakeLlm([_FINDINGS_JSON, _FINDINGS_JSON]),
    )
    await _run(refused, h.seed.target_id)

    runs = await _runs(h)
    assert {run.level for run in runs} == {"main", "pr", "sub"}
    # The review itself succeeded; publishing is what failed, and the failure
    # closed the nodes that owned it (the reviewer sub run is done)
    by_level = {run.level: run for run in runs}
    assert by_level["main"].status == "failed"
    assert by_level["pr"].status == "failed"
    assert by_level["sub"].status == "done"

    # When the same target runs again — what `POST /api/sessions/{id}/retry`
    # queues after it puts the target and session back to `queued` (a terminal
    # target is skipped by the job's own guard, spec 10.5)
    await _requeue(h, h.seed.target_id)
    retried = _harness_for(h, target_id=h.seed.target_id)
    await _run(retried, h.seed.target_id)

    # Then the reused nodes are running again and finish done under one PR
    # orchestrator, and the retry is a second sub run rather than a rewritten one
    runs = await _runs(h)
    main = next(run for run in runs if run.level == "main")
    pr_runs = [run for run in runs if run.level == "pr"]
    sub_runs = [run for run in runs if run.level == "sub"]
    assert len(pr_runs) == 1
    assert len(sub_runs) == 2
    assert main.status == "done" and main.error is None
    assert pr_runs[0].status == "done" and pr_runs[0].error is None
    # The root was reopened and announced starting again, exactly once per attempt
    assert _types(await _events(h, main.id)).count("agent.started") == 2
