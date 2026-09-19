"""ARQ job: run one review target and publish its results (spec 10.5-10.9).

`review_target(ctx, session_id, target_id)` is the unit of work the server
enqueues once per PR target. It loads the target graph, guards on terminal and
cancelled state, runs the single-pass harness, persists findings and usage,
publishes to GitHub, and recomputes the parent session. Transient failures
retry with bounded exponential backoff; permanent failures (invalid repo
config) fail the target immediately. One target's failure never affects
another — they are separate jobs.
"""

from __future__ import annotations

import datetime as dt
import logging
import time
import uuid
from dataclasses import dataclass
from typing import NotRequired, TypedDict

from arq import Retry
from sqlalchemy.ext.asyncio import AsyncSession

from slopolis_core.config.repo_config import RepoConfig
from slopolis_core.domain import TargetStatus
from slopolis_core.github.models import GitHubPullRequest
from slopolis_core.review.harness import ReviewResult
from slopolis_core.settings import get_settings
from slopolis_db.models import Finding, SessionTargetRun
from worker.deps import (
    InstallationClients,
    InstallationRef,
    ReviewContext,
    SessionFactory,
)
from worker.jobs import persistence
from worker.jobs.loading import LoadOutcome, TargetJob, load_target
from worker.jobs.model_selection import resolve_model
from worker.jobs.publish import PublishPlan, publish
from worker.jobs.publishing import InlineTarget, inline_targets
from worker.jobs.repo_config_load import PermanentTargetError, load_repo_config

__all__ = ["PermanentTargetError", "WorkerCtx", "review_target"]

_LOG = logging.getLogger("worker.review_target")


class WorkerCtx(TypedDict):
    """The ARQ job context assembled by ``WorkerSettings`` startup hooks."""

    review: ReviewContext
    session_factory: SessionFactory
    job_try: NotRequired[int]


@dataclass(frozen=True, slots=True)
class _Ids:
    """Scalar ids captured before any rollback so failure writes stay valid."""

    session: uuid.UUID
    target: uuid.UUID
    run: uuid.UUID
    workspace: uuid.UUID


@dataclass(frozen=True, slots=True)
class _AttemptPlan:
    """The resolved clients, harness output, and publish inputs for one attempt."""

    clients: InstallationClients
    result: ReviewResult
    pull: GitHubPullRequest
    repo_config: RepoConfig


async def review_target(ctx: WorkerCtx, session_id: str, target_id: str) -> None:
    """Run one review target end to end, retrying transient failures via ARQ."""
    review = ctx["review"]
    async with ctx["session_factory"]() as db:
        load = await load_target(db, session_id, target_id)
        if load.outcome is LoadOutcome.MISSING:
            _LOG.warning(
                "target not found",
                extra={"session_id": session_id, "target_id": target_id},
            )
            return
        if load.outcome is LoadOutcome.SESSION_CANCELLED and load.job is not None:
            await persistence.mark_target_cancelled(db, load.job.target)
            await persistence.recompute_session(db, load.job.session_id)
            await db.commit()
            return
        if load.outcome in (LoadOutcome.TARGET_TERMINAL, LoadOutcome.SESSION_TERMINAL):
            return
        job = load.job
        if job is None:
            return
        run = await persistence.start_run(db, job.target, now=_now())
        await db.commit()
        await _run_attempt(ctx=ctx, db=db, review=review, job=job, run=run)


async def _run_attempt(
    *,
    ctx: WorkerCtx,
    db: AsyncSession,
    review: ReviewContext,
    job: TargetJob,
    run: SessionTargetRun,
) -> None:
    """Execute one attempt and record its outcome, retrying or failing."""
    ids = _Ids(
        session=job.session_id,
        target=job.target_id,
        run=run.id,
        workspace=job.workspace.id,
    )
    started = time.monotonic()
    try:
        plan = await _execute(db=db, review=review, job=job)
    except PermanentTargetError as exc:
        await _fail_permanently(db=db, job=job, run=run, ids=ids, exc=exc)
        return
    except Exception as exc:  # classified as retryable or terminal below
        await _fail_retryable(ctx=ctx, db=db, job=job, run=run, ids=ids, exc=exc)
        return

    await _persist_and_publish(db=db, job=job, run=run, ids=ids, plan=plan)
    duration_ms = int((time.monotonic() - started) * 1000)
    await persistence.finish_run(
        db, target=job.target, run=run, now=_now(), duration_ms=duration_ms
    )
    await persistence.recompute_session(db, job.session_id)
    await db.commit()
    _LOG.info(
        "target done",
        extra={
            "session_id": str(ids.session),
            "target_id": str(ids.target),
            "findings": len(plan.result.findings),
            "tokens": plan.result.tokens,
        },
    )


async def _execute(
    *, db: AsyncSession, review: ReviewContext, job: TargetJob
) -> _AttemptPlan:
    """Resolve the model, re-read the PR, load repo config, and run the harness."""
    model = await resolve_model(
        db,
        workspace_id=job.workspace.id,
        fallback_model=job.session.model,
        fallback_provider=job.session.provider,
    )
    clients = await review.build_clients(InstallationRef(job.installation.installation_id))
    pull = await clients.reader.get_pull_request(job.repo_full_name, job.target.number)
    repo_config = await load_repo_config(
        clients.reader, job.repo_full_name, pull.head_sha, review.config
    )
    harness = review.build_harness(clients.reader)
    result = await harness.review(
        repo_full_name=job.repo_full_name,
        number=job.target.number,
        session_prompt=job.session.prompt,
        repo_config=repo_config,
        model=model.model_id,
        provider=model.provider,
    )
    return _AttemptPlan(clients=clients, result=result, pull=pull, repo_config=repo_config)


async def _persist_and_publish(
    *,
    db: AsyncSession,
    job: TargetJob,
    run: SessionTargetRun,
    ids: _Ids,
    plan: _AttemptPlan,
) -> None:
    """Persist findings, publish to GitHub, then stamp findings and usage."""
    job.target.head_branch = plan.pull.head_branch
    inline = inline_targets(
        plan.result.findings,
        threshold=plan.repo_config.review.severity_threshold,
        suggestions=plan.repo_config.output.suggestions,
    )
    rows = await persistence.persist_findings(
        db, target_id=ids.target, run_id=ids.run, findings=plan.result.findings
    )
    outcome = await publish(
        plan.clients.publisher,
        PublishPlan(
            repo_full_name=job.repo_full_name,
            number=job.target.number,
            head_sha=plan.pull.head_sha,
            result=plan.result,
            inline=inline,
            repo_config=plan.repo_config,
            session_url=_session_url(ids.session),
            status=str(TargetStatus.DONE),
        ),
    )
    _stamp_posted(rows=rows, inline=inline, comment_ids=outcome.inline_comment_ids)
    await persistence.persist_usage(
        db,
        workspace_id=ids.workspace,
        session_id=ids.session,
        target_id=ids.target,
        model_id=plan.result.model,
        provider=plan.result.provider,
        result=plan.result,
    )
    persistence.record_run_usage(job.target, run, plan.result)


def _stamp_posted(
    *, rows: list[Finding], inline: list[InlineTarget], comment_ids: list[int]
) -> None:
    """Mark each inline-published finding row with its GitHub comment id."""
    for target, comment_id in zip(inline, comment_ids, strict=False):
        row = rows[target.source_index]
        row.posted = True
        row.github_comment_id = comment_id


async def _fail_permanently(
    *,
    db: AsyncSession,
    job: TargetJob,
    run: SessionTargetRun,
    ids: _Ids,
    exc: Exception,
) -> None:
    """Mark the target failed without retrying (e.g. invalid config)."""
    await db.rollback()
    error = f"{type(exc).__name__}: {exc}"
    await persistence.fail_target(db, target=job.target, run=run, error=error, now=_now())
    await persistence.recompute_session(db, job.session_id)
    await db.commit()
    _LOG.error(
        "target failed permanently",
        extra={"session_id": str(ids.session), "target_id": str(ids.target), "error": error},
    )


async def _fail_retryable(
    *,
    ctx: WorkerCtx,
    db: AsyncSession,
    job: TargetJob,
    run: SessionTargetRun,
    ids: _Ids,
    exc: Exception,
) -> None:
    """Record the failed attempt and retry with backoff, or fail the target."""
    await db.rollback()
    config = ctx["review"].config
    attempt = int(ctx.get("job_try", 1))
    error = f"{type(exc).__name__}: {exc}"
    if attempt < config.max_tries:
        await persistence.fail_run(db, run=run, error=error, now=_now())
        await db.commit()
        _LOG.warning(
            "target attempt failed; retrying",
            extra={
                "session_id": str(ids.session),
                "target_id": str(ids.target),
                "attempt": attempt,
                "error": error,
            },
        )
        raise Retry(defer=config.backoff_seconds(attempt)) from exc
    await persistence.fail_target(db, target=job.target, run=run, error=error, now=_now())
    await persistence.recompute_session(db, job.session_id)
    await db.commit()
    _LOG.error(
        "target failed after retries",
        extra={"session_id": str(ids.session), "target_id": str(ids.target), "error": error},
    )


def _session_url(session_id: uuid.UUID) -> str:
    """Return the app permalink for a session."""
    return f"{get_settings().app_url}/sessions/{session_id}"


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)
