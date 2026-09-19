"""Behavior tests for the review_target job (spec 10.5-10.9).

Every test runs against in-memory SQLite with fake LLM/GitHub clients; no
network, no Redis. Given/When/Then structure with fakes asserting on the exact
values the job produces.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from arq import Retry
from sqlalchemy import select
from worker.config import WorkerConfig
from worker.deps import SessionFactory
from worker.jobs.review_target import review_target
from worker_fakes import (
    CATALOG_MODEL,
    HEAD_BRANCH,
    HEAD_SHA,
    FakeLlm,
    FakeReader,
)
from worker_seed import Harness, seed_and_build

from slopolis_core.github.errors import GitHubError
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


async def _run(h: Harness) -> None:
    await review_target(h.ctx, str(h.seed.session_id), str(h.seed.target_id))


async def _target(h: Harness) -> SessionTarget:
    async with h.session_factory() as db:
        loaded = await db.get(SessionTarget, h.seed.target_id)
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
