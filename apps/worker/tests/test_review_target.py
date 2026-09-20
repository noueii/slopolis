"""Behavior tests for the review_target job (spec 10.5-10.9).

Every test runs against in-memory SQLite with fake LLM/GitHub clients; no
network, no Redis. Given/When/Then structure with fakes asserting on the exact
values the job produces.
"""

from __future__ import annotations

import asyncio
import logging
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
    PR_NUMBER,
    REPO_FULL_NAME,
    FakeLlm,
    FakePublisher,
    FakeReader,
    FakeRedis,
)
from worker_seed import Harness, build_harness, seed_and_build

from slopolis_core.domain import TargetStatus
from slopolis_core.github.errors import GitHubAuthError, GitHubError, GitHubRateLimitError
from slopolis_core.llm.models import ChatMessage, CompletionResult
from slopolis_db.models import (
    AgentEventRow,
    AgentRun,
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

#: A review that found nothing. Still a completed review — with usage and a
#: summary — so a publish retry must post it rather than lose it to an empty list.
_CLEAN_JSON = '{"findings": []}'

#: A repo config that keeps the inline and check-run surfaces and drops the summary.
_NO_SUMMARY_CONFIG = "output:\n  summary_comment: false\n"

#: What the client raises when an installation was never granted write scope —
#: the live failure this lifecycle guards against.
_PERMISSION_REFUSAL = GitHubAuthError(
    "GitHub authorization failed during async_create_issue_comment (HTTP 403)"
)

#: What it raises when a write is throttled instead; retrying this one is right.
_THROTTLED = GitHubRateLimitError("GitHub rate limit hit during upsert_summary_comment")

#: What the client raises when the installation cannot post the advisory check run
#: — a warning at pre-flight rather than a refusal, since the comments the review
#: was asked for post with ``pull_requests: write`` alone (spec 10.7).
_CHECK_REFUSAL = GitHubAuthError(
    "GitHub authorization failed during async_create_check_run (HTTP 403)"
)

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


async def _run(
    h: Harness, target_id: uuid.UUID | None = None, *, mode: str = "review"
) -> None:
    await review_target(
        h.ctx,
        str(h.seed.session_id),
        str(target_id if target_id is not None else h.seed.target_id),
        mode,
    )


async def _requeue_target(h: Harness, target_id: uuid.UUID | None = None) -> None:
    """Commit what the retry endpoint leaves behind: the target back on the queue.

    A retry flips the target (and its session) back to ``queued`` before it
    enqueues the job, so a test that calls the job directly has to do the same or
    the load guards skip it as terminal.
    """
    async with h.session_factory() as db:
        target = await db.get(
            SessionTarget, target_id if target_id is not None else h.seed.target_id
        )
        assert target is not None
        target.status = TargetStatus.QUEUED
        session = await db.get(ReviewSession, h.seed.session_id)
        assert session is not None
        session.status = "queued"
        session.finished_at = None
        await db.commit()


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


async def _pr_events(h: Harness) -> list[AgentEventRow]:
    """Every event on the target's PR node, in order (spec §15)."""
    async with h.session_factory() as db:
        run = (
            await db.execute(
                select(AgentRun).where(
                    AgentRun.target_id == h.seed.target_id,
                    AgentRun.level == "pr",
                )
            )
        ).scalar_one()
        rows = await db.execute(
            select(AgentEventRow)
            .where(AgentEventRow.run_id == run.id)
            .order_by(AgentEventRow.seq)
        )
    return list(rows.scalars())


async def _pr_node_summary(h: Harness) -> str:
    """The summary the target's PR node ended with in the run tree (spec §15)."""
    return str((await _pr_events(h))[-1].payload["summary"])


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
    # ... because the lookup found no earlier summary to edit on this pull request
    assert h.seed.publisher.summary_lookups == [(REPO_FULL_NAME, PR_NUMBER)]
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


async def test_a_rerun_edits_the_existing_summary_comment(
    session_factory: SessionFactory,
) -> None:
    # Given a pull request that already carries the summary comment of an earlier
    # publish of this session
    publisher = FakePublisher(existing_summary_id=101)
    h = await seed_and_build(
        session_factory, publisher=publisher, llm=FakeLlm([_FINDINGS_JSON])
    )

    # When the job runs again
    await _run(h)

    # Then the publish asked this pull request for that comment and edited it
    # instead of posting a second summary
    assert publisher.summary_lookups == [(REPO_FULL_NAME, PR_NUMBER)]
    assert len(publisher.summaries) == 1
    repo_name, number, body, existing_id = publisher.summaries[0]
    assert (repo_name, number, existing_id) == (REPO_FULL_NAME, PR_NUMBER, 101)

    # ... with the body a later run can find again by its marker
    assert body.startswith("## slopolis review")


async def test_a_failed_summary_lookup_still_publishes_the_review(
    session_factory: SessionFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Given a pull request whose comment listing GitHub throttles
    publisher = FakePublisher(lookup_fail_with=_THROTTLED)
    h = await seed_and_build(
        session_factory, publisher=publisher, llm=FakeLlm([_FINDINGS_JSON])
    )

    # When the job runs
    with caplog.at_level(logging.WARNING, logger="worker.jobs.publish"):
        await _run(h)

    # Then the review still posts, as a new comment because the earlier one's id
    # never arrived, and the target completes rather than failing
    assert len(publisher.summaries) == 1
    assert publisher.summaries[0][3] is None
    assert len(publisher.inlines) == 1
    assert (await _target(h)).status == "done"
    assert (await _session(h)).status == "done"

    # ... with the reason the comment could not roll recorded for the operator
    warned = [
        record
        for record in caplog.records
        if "summary comment lookup failed" in record.getMessage()
    ]
    assert len(warned) == 1
    assert "GitHubRateLimitError" in str(getattr(warned[0], "reason", ""))


async def test_a_disabled_summary_comment_is_never_looked_up(
    session_factory: SessionFactory,
) -> None:
    # Given a repo whose config turns the summary comment off
    h = await seed_and_build(
        session_factory,
        reader=FakeReader(config_text=_NO_SUMMARY_CONFIG),
        publisher=FakePublisher(existing_summary_id=101),
        llm=FakeLlm([_FINDINGS_JSON]),
    )

    # When the job runs
    await _run(h)

    # Then nothing touched the summary surface at all
    assert h.seed.publisher.summary_lookups == []
    assert h.seed.publisher.summaries == []

    # ... while the surfaces the config kept still post
    assert len(h.seed.publisher.inlines) == 1
    assert len(h.seed.publisher.checks) == 1


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


async def test_publish_permission_refusal_fails_target_and_keeps_findings(
    session_factory: SessionFactory,
) -> None:
    # Given a GitHub App installation that refuses every write (403)
    h = await seed_and_build(
        session_factory,
        publisher=FakePublisher(fail_with=_PERMISSION_REFUSAL),
        llm=FakeLlm([_FINDINGS_JSON]),
    )

    # When the job runs, it gives up on the target instead of paying for the same
    # review a second time
    await _run(h)

    # Then exactly one attempt was made and the target is failed with the advice
    # the operator needs to fix the permission
    runs = await _runs(h)
    assert len(runs) == 1
    assert runs[0].attempt == 1
    assert runs[0].status == "failed"
    assert runs[0].error is not None
    assert "GitHubAuthError" in runs[0].error
    assert "(HTTP 403)" in runs[0].error
    assert "posting the review needs Pull requests 'Read & write'" in runs[0].error
    assert "then approve the update for the installation" in runs[0].error
    assert (await _target(h)).status == "failed"
    assert (await _session(h)).status == "failed"

    # ... no attempt is left open, and the failed attempt's spend is recorded
    assert all(run.status != "running" for run in runs)
    usage = await _usage(h)
    assert len(usage) == 1
    assert usage[0].total_tokens == 15

    # ... and the review the target already paid for is still visible in the app,
    # unposted because GitHub never accepted it
    findings = await _findings(h)
    assert len(findings) == 2
    assert [row.run_id for row in findings] == [runs[0].id, runs[0].id]
    assert all(row.posted is False for row in findings)
    assert all(row.github_comment_id is None for row in findings)


async def test_refused_check_run_does_not_fail_the_published_review(
    session_factory: SessionFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Given an installation that posts comments but cannot post a check run
    publisher = FakePublisher(check_fail_with=_CHECK_REFUSAL)
    h = await seed_and_build(
        session_factory, publisher=publisher, llm=FakeLlm([_FINDINGS_JSON])
    )

    # When the target runs
    with caplog.at_level(logging.WARNING, logger="worker.review_target"):
        await _run(h)

    # Then the review the user asked for is published: the summary and the inline
    # comment reached the pull request, and only the advisory surface was refused
    assert publisher.checks == []
    assert len(publisher.summaries) == 1
    assert len(publisher.inlines) == 1

    # ... the target completes rather than failing, with no retry to duplicate the
    # comments that are already on the pull request
    runs = await _runs(h)
    assert [run.status for run in runs] == ["done"]
    assert runs[0].error is None
    assert (await _target(h)).status == "done"
    assert (await _session(h)).status == "done"

    # ... the findings that posted are stamped, not rolled back with the refused
    # artifact
    findings = {row.line: row for row in await _findings(h)}
    assert findings[3].posted is True
    assert findings[3].github_comment_id == 201
    assert findings[None].posted is False

    # ... the skip is visible in the PR node's summary together with its reason,
    # so a missing check run is never read as a review that failed to post
    summary = await _pr_node_summary(h)
    assert "without a check run" in summary
    assert "GitHubAuthError" in summary
    assert "(HTTP 403)" in summary

    # ... and the log carries the same reason for the operator reading it live
    skipped = [
        record for record in caplog.records if "check run skipped" in record.getMessage()
    ]
    assert len(skipped) == 1
    assert "GitHubAuthError" in str(getattr(skipped[0], "reason", ""))


async def test_publish_throttled_retries_and_drops_the_failed_attempts_findings(
    session_factory: SessionFactory,
) -> None:
    # Given a publish throttled on the first of two permitted attempts
    publisher = FakePublisher(fail_with=_THROTTLED)
    h = await seed_and_build(
        session_factory,
        publisher=publisher,
        llm=FakeLlm([_FINDINGS_JSON, _FINDINGS_JSON]),
        config=WorkerConfig(WORKER_MAX_TRIES=2, WORKER_RETRY_BACKOFF_S=1),
        job_try=1,
    )

    # When the job runs it defers a retry, closing the attempt it failed
    with pytest.raises(Retry):
        await _run(h)

    # Then the target is still running for the retry, with no attempt left open
    runs = await _runs(h)
    assert len(runs) == 1
    assert runs[0].status == "failed"
    assert runs[0].error is not None and "GitHubRateLimitError" in runs[0].error
    assert (await _target(h)).status == "running"
    assert all(run.status != "running" for run in runs)

    # ... and the review it could not post is still readable: the findings were
    # committed before publishing, so a throttled PR does not erase the work
    assert len(await _findings(h)) == 2

    # When GitHub accepts the retry's writes
    publisher.fail_with = None
    await _run(h)

    # Then attempt 2 is the only attempt whose findings remain — the failed
    # attempt's unposted rows were superseded when it started — and the posted one
    # carries its comment id
    runs = await _runs(h)
    assert len(runs) == 2
    assert runs[1].attempt == 2
    assert runs[1].status == "done"
    assert all(run.status != "running" for run in runs)
    findings = {row.line: row for row in await _findings(h)}
    assert set(findings) == {3, None}
    assert all(row.run_id == runs[1].id for row in findings.values())
    assert findings[3].posted is True
    assert findings[3].github_comment_id == 201
    assert findings[None].posted is False
    assert (await _target(h)).status == "done"
    assert (await _session(h)).status == "done"


async def _throttled_publish(session_factory: SessionFactory, *, findings: str) -> Harness:
    """Run the review whose publish GitHub throttled away, leaving it unposted.

    The last permitted try fails permanently at the publish, which is the state a
    publish retry starts from: findings and usage committed, target failed, and an
    attempt that recorded the review's tokens alongside the GitHub error.
    """
    h = await seed_and_build(
        session_factory,
        publisher=FakePublisher(fail_with=_THROTTLED),
        llm=FakeLlm([findings]),
        config=WorkerConfig(WORKER_MAX_TRIES=1, WORKER_RETRY_BACKOFF_S=1),
        job_try=1,
    )
    await _run(h)
    assert (await _target(h)).status == "failed"
    return h


async def test_a_publish_retry_posts_the_persisted_review_without_a_model_call(
    session_factory: SessionFactory,
) -> None:
    # Given a review that ran and whose publish GitHub throttled away
    h = await _throttled_publish(session_factory, findings=_FINDINGS_JSON)
    assert len(await _findings(h)) == 2

    # When GitHub accepts writes again and the target is retried in publish mode
    h.seed.publisher.fail_with = None
    await _requeue_target(h)
    await _run(h, mode="publish")

    # Then the review the target already paid for is what posted, in full
    assert len(h.seed.publisher.summaries) == 1
    summary = h.seed.publisher.summaries[0][2]
    assert "boom" in summary and "nit" in summary
    assert "15 tokens" in summary
    assert "$0.0020" in summary
    assert len(h.seed.publisher.inlines) == 1
    assert h.seed.publisher.inlines[0][3] == HEAD_SHA
    assert len(h.seed.publisher.checks) == 1
    assert h.seed.publisher.checks[0][2] == "failure"

    # ... and the model was never asked for it again: the one turn the fake
    # recorded is the first review's
    assert h.seed.llm.models == [CATALOG_MODEL]

    # ... the posted finding is stamped with its comment id while the one with no
    # line stays in the summary, on the rows the review attempt persisted
    runs = await _runs(h)
    findings = {row.line: row for row in await _findings(h)}
    assert set(findings) == {3, None}
    assert all(row.run_id == runs[0].id for row in findings.values())
    assert findings[3].posted is True
    assert findings[3].github_comment_id == 201
    assert findings[None].posted is False

    # ... the review is billed once: a publish retry records no second usage row
    assert len(await _usage(h)) == 1

    # ... the new attempt is recorded like any other, carrying the review's tokens
    # so a further failure is still read as a publish retry rather than a reason
    # to review again
    assert [run.attempt for run in runs] == [1, 2]
    assert runs[1].status == "done"
    assert runs[1].tokens == 15
    assert (await _target(h)).status == "done"
    assert (await _session(h)).status == "done"

    # ... and the tree says what happened: the reused PR node re-published the
    # review without calling a model, without duplicating its findings as new ones
    events = await _pr_events(h)
    messages = [
        str(event.payload["summary"])
        for event in events
        if event.type == "agent.message"
    ]
    assert "re-published the existing review without a model call" in messages
    assert [event.type for event in events].count("agent.finding") == 2
    summary = await _pr_node_summary(h)
    assert "publish retry" in summary
    assert "2 finding(s)" in summary


async def test_a_publish_retry_posts_the_summary_of_a_clean_review(
    session_factory: SessionFactory,
) -> None:
    # Given a review that found nothing, whose one attempt was throttled away
    h = await _throttled_publish(session_factory, findings=_CLEAN_JSON)
    assert await _findings(h) == []

    # When the target is retried in publish mode
    h.seed.publisher.fail_with = None
    await _requeue_target(h)
    await _run(h, mode="publish")

    # Then the clean review still posts — an empty review is a result, not a
    # reason to publish nothing and lose the pass the user paid for
    assert len(h.seed.publisher.summaries) == 1
    summary = h.seed.publisher.summaries[0][2]
    assert "No findings." in summary
    assert "15 tokens" in summary
    assert h.seed.publisher.inlines == []
    assert len(h.seed.publisher.checks) == 1
    assert h.seed.publisher.checks[0][2] == "success"
    assert (await _target(h)).status == "done"
    assert (await _session(h)).status == "done"
    assert h.seed.llm.models == [CATALOG_MODEL]


async def test_a_publish_retry_refused_by_github_fails_the_target_the_same_way(
    session_factory: SessionFactory,
) -> None:
    # Given a review whose publish was throttled away
    h = await _throttled_publish(session_factory, findings=_FINDINGS_JSON)

    # When the retry's writes are refused a permission instead
    h.seed.publisher.fail_with = _PERMISSION_REFUSAL
    await _requeue_target(h)
    await _run(h, mode="publish")

    # Then the target fails naming the scope to grant, exactly as the attempt that
    # first tried to publish would have
    runs = await _runs(h)
    assert len(runs) == 2
    assert runs[1].status == "failed"
    assert runs[1].error is not None
    assert "GitHubAuthError" in runs[1].error
    assert "(HTTP 403)" in runs[1].error
    assert "posting the review needs Pull requests 'Read & write'" in runs[1].error
    assert (await _target(h)).status == "failed"
    assert (await _session(h)).status == "failed"

    # ... and the attempt says it was a publish retry, so the failure is not read
    # as a model run that spent nothing
    assert "publish retry" in runs[1].error

    # ... while the review it could not post is still visible in the app
    findings = await _findings(h)
    assert len(findings) == 2
    assert all(row.posted is False for row in findings)


async def test_a_publish_retry_keeps_a_refused_check_run_best_effort(
    session_factory: SessionFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Given a review whose publish was throttled away, and an installation that
    # cannot post the advisory check run
    h = await _throttled_publish(session_factory, findings=_FINDINGS_JSON)
    h.seed.publisher.fail_with = None
    h.seed.publisher.check_fail_with = _CHECK_REFUSAL

    # When the target is retried in publish mode
    with caplog.at_level(logging.WARNING, logger="worker.review_target"):
        await _requeue_target(h)
        await _run(h, mode="publish")

    # Then the review the user asked for still posts, and only the advisory surface
    # is missing
    assert h.seed.publisher.checks == []
    assert len(h.seed.publisher.summaries) == 1
    assert len(h.seed.publisher.inlines) == 1
    assert (await _target(h)).status == "done"
    assert (await _session(h)).status == "done"

    # ... and the PR node says both things: that this was a publish retry, and why
    # the check run is not on the pull request
    summary = await _pr_node_summary(h)
    assert "publish retry" in summary
    assert "without a check run" in summary
    assert "GitHubAuthError" in summary


async def test_a_throttled_publish_retry_keeps_the_review_for_the_next_try(
    session_factory: SessionFactory,
) -> None:
    # Given a review whose publish was throttled away and a retry whose publish is
    # throttled too, with a second try of its own still permitted
    h = await _throttled_publish(session_factory, findings=_FINDINGS_JSON)
    await _requeue_target(h)
    second_try = build_harness(
        session_factory=session_factory,
        seed=h.seed,
        config=WorkerConfig(WORKER_MAX_TRIES=2, WORKER_RETRY_BACKOFF_S=1),
        job_try=1,
    )
    with pytest.raises(Retry):
        await _run(second_try, mode="publish")

    # Then the attempt is failed but the review is still there to post, unposted
    assert [run.status for run in await _runs(h)] == ["failed", "failed"]
    assert len(await _findings(h)) == 2

    # When the queue's next try runs, it publishes that same review, not a fresh one
    h.seed.publisher.fail_with = None
    await _run(second_try, mode="publish")

    # Then the review reaches the pull request without the model being asked again
    assert len(h.seed.publisher.summaries) == 1
    assert len(await _findings(h)) == 2
    assert (await _target(h)).status == "done"
    assert (await _session(h)).status == "done"
    assert h.seed.llm.models == [CATALOG_MODEL]
    assert len(await _usage(h)) == 1


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
