"""Database writes for one review target's lifecycle (spec 10.5 / 10.9).

Every function is a small, explicit state transition: start a run, persist the
harness output, finish (or fail) the attempt, and recompute the parent session.
Nothing here publishes or calls the network; the job orchestrates.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any, cast

from sqlalchemy import delete, func, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from slopolis_core.domain import SessionStatus, TargetStatus
from slopolis_core.findings import Finding as CoreFinding
from slopolis_core.harness import AgentStatus
from slopolis_core.review.harness import ReviewResult
from slopolis_db.models import Finding, SessionTarget, SessionTargetRun, UsageRecord
from worker.jobs.agent_runs import finish_session_main_run

__all__ = [
    "discard_unposted_findings",
    "fail_run",
    "fail_target",
    "finish_run",
    "mark_target_cancelled",
    "next_attempt",
    "persist_findings",
    "persist_usage",
    "recompute_session",
    "record_run_usage",
    "start_run",
]

_ERROR_LIMIT = 2000


async def next_attempt(db: AsyncSession, target_id: uuid.UUID) -> int:
    """Return the 1-based attempt number for a new run row."""
    count = await db.scalar(
        select(func.count()).select_from(SessionTargetRun).where(
            SessionTargetRun.target_id == target_id
        )
    )
    return int(count or 0) + 1


async def start_run(
    db: AsyncSession, target: SessionTarget, *, now: dt.datetime
) -> SessionTargetRun:
    """Mark ``target`` running and create its next attempt row."""
    target.status = TargetStatus.RUNNING
    run = SessionTargetRun(
        target_id=target.id,
        attempt=await next_attempt(db, target.id),
        status=TargetStatus.RUNNING,
        started_at=now,
    )
    db.add(run)
    await db.flush()
    return run


async def persist_findings(
    db: AsyncSession, *, target_id: uuid.UUID, run_id: uuid.UUID, findings: list[CoreFinding]
) -> list[Finding]:
    """Insert one row per grounded finding; return the inserted rows in order."""
    rows = [
        Finding(
            target_id=target_id,
            run_id=run_id,
            path=finding.path,
            line=finding.line,
            severity=str(finding.severity),
            category=finding.category,
            message=finding.message,
            suggestion=finding.suggestion,
            confidence=finding.confidence,
            posted=False,
        )
        for finding in findings
    ]
    db.add_all(rows)
    await db.flush()
    return rows


async def discard_unposted_findings(db: AsyncSession, target_id: uuid.UUID) -> int:
    """Delete a target's findings that never reached GitHub; return how many.

    A retried attempt persists its findings *before* it publishes (spec 10.7), so
    a publish that fails leaves rows behind that the retry would duplicate. Only
    the unposted ones go: a posted row is the app's only link to a comment that
    exists on the PR, and deleting it would lose that link, not just a duplicate
    (spec 10.9).
    """
    result = await db.execute(
        delete(Finding)
        .where(Finding.target_id == target_id, Finding.posted.is_(False))
        # The deleted rows belong to an attempt whose session is about to end;
        # syncing the identity map would only refresh them to say goodbye.
        .execution_options(synchronize_session=False)
    )
    await db.flush()
    return cast("CursorResult[Any]", result).rowcount


async def persist_usage(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    session_id: uuid.UUID,
    target_id: uuid.UUID,
    model_id: str,
    provider: str,
    result: ReviewResult,
) -> UsageRecord:
    """Record one usage row for the completed review pass."""
    usage = UsageRecord(
        workspace_id=workspace_id,
        session_id=session_id,
        target_id=target_id,
        model_id=model_id,
        provider=provider,
        prompt_tokens=0,
        completion_tokens=0,
        total_tokens=result.tokens,
        cost_usd=Decimal(str(result.cost_usd)),
    )
    db.add(usage)
    await db.flush()
    return usage


def record_run_usage(target: SessionTarget, run: SessionTargetRun, result: ReviewResult) -> None:
    """Copy tokens/cost onto the target and attempt, stamping the duration."""
    target.tokens = result.tokens
    target.cost_usd = Decimal(str(result.cost_usd))
    run.tokens = result.tokens
    run.cost_usd = Decimal(str(result.cost_usd))


async def finish_run(
    db: AsyncSession,
    *,
    target: SessionTarget,
    run: SessionTargetRun,
    now: dt.datetime,
    duration_ms: int | None,
) -> None:
    """Mark the attempt and its target done."""
    run.status = TargetStatus.DONE
    run.finished_at = now
    target.status = TargetStatus.DONE
    target.duration_ms = duration_ms
    await db.flush()


async def fail_run(
    db: AsyncSession, *, run: SessionTargetRun, error: str, now: dt.datetime
) -> None:
    """Mark one attempt failed, recording the error; leave the target running."""
    run.status = TargetStatus.FAILED
    run.finished_at = now
    run.error = error[:_ERROR_LIMIT]
    await db.flush()


async def fail_target(
    db: AsyncSession,
    *,
    target: SessionTarget,
    run: SessionTargetRun,
    error: str,
    now: dt.datetime,
) -> None:
    """Mark the attempt and target failed, recording the error message."""
    await fail_run(db, run=run, error=error, now=now)
    target.status = TargetStatus.FAILED
    await db.flush()


async def mark_target_cancelled(db: AsyncSession, target: SessionTarget) -> None:
    """Mark a target cancelled (session was cancelled before it ran)."""
    target.status = TargetStatus.CANCELLED
    await db.flush()


#: The run status a terminal session's ``main`` node ends in (spec v2 §15).
_SESSION_RUN_STATUS: dict[SessionStatus, AgentStatus] = {
    SessionStatus.DONE: AgentStatus.DONE,
    SessionStatus.FAILED: AgentStatus.FAILED,
    SessionStatus.CANCELLED: AgentStatus.CANCELLED,
}


async def recompute_session(db: AsyncSession, session_id: uuid.UUID) -> SessionStatus:
    """Recompute and persist the parent session status from its targets.

    All targets terminal: ``failed`` if any failed, ``cancelled`` if every one
    was cancelled, else ``done``. Otherwise the session stays ``running``. A
    terminal session also closes its ``main`` run (spec v2 §15), the session's
    aggregation point in the run tree.
    """
    from slopolis_db.models import ReviewSession

    session = await db.get(ReviewSession, session_id)
    if session is None:
        return SessionStatus.RUNNING

    statuses = list(
        (
            await db.execute(
                select(SessionTarget.status).where(SessionTarget.session_id == session_id)
            )
        )
        .scalars()
        .all()
    )
    done = _terminal_rollup(statuses)
    session.status = done
    if done is not SessionStatus.RUNNING:
        session.finished_at = dt.datetime.now(dt.UTC)
        run_status = _SESSION_RUN_STATUS.get(done)
        if run_status is not None:
            await finish_session_main_run(
                db,
                session_id,
                run_status,
                summary=f"{len(statuses)} target(s) finished as {done.value}",
            )
    await db.flush()
    return done


def _terminal_rollup(statuses: list[str]) -> SessionStatus:
    """Collapse per-target statuses into one session status."""
    if not statuses:
        return SessionStatus.RUNNING
    terminal = {
        TargetStatus.DONE,
        TargetStatus.FAILED,
        TargetStatus.CANCELLED,
        TargetStatus.SKIPPED,
    }
    if any(status not in terminal for status in statuses):
        return SessionStatus.RUNNING
    if TargetStatus.FAILED in statuses:
        return SessionStatus.FAILED
    if all(status == TargetStatus.CANCELLED for status in statuses):
        return SessionStatus.CANCELLED
    return SessionStatus.DONE
