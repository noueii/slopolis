"""ARQ job: run one review target and publish its results (spec 10.5-10.9).

`review_target(ctx, session_id, target_id)` is the unit of work the server
enqueues once per PR target. It loads the target graph, guards on terminal and
cancelled state, opens the target's nodes in the Harness V1.1 run tree (the
session's `main` run and this target's `pr` run, then the reviewer's `sub` run),
runs the single-pass harness, persists findings and usage, publishes to GitHub,
and recomputes the parent session. Transient failures retry with bounded
exponential backoff; permanent failures (invalid repo config) fail the target
immediately. One target's failure never affects another — they are separate
jobs.

The target's per-repository and per-installation caps are applied here too
(spec 10.5): before an attempt starts, the job holds a run slot per capped
dimension (see :mod:`worker.jobs.slots`), waiting in-job for one and, if the wait
runs out, deferring itself back onto the queue. The gate never marks a target
failed — the queue decides a target's fate, and the job only runs what the gate
let through.
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
from slopolis_core.findings import Finding as CoreFinding
from slopolis_core.github.models import GitHubPullRequest
from slopolis_core.harness import (
    AgentRuntime,
    AgentSpec,
    AgentStatus,
    AssistantTurn,
    EventType,
    Message,
    ModelChoice,
    Policy,
    StaticResolver,
    ToolSpec,
    agent_spec,
)
from slopolis_core.review.harness import ReviewHarness, ReviewResult
from slopolis_core.settings import get_settings
from slopolis_db.models import Finding, SessionTargetRun
from worker.deps import (
    InstallationClients,
    InstallationRef,
    ReviewContext,
    SessionFactory,
)
from worker.jobs import persistence
from worker.jobs.agent_runs import RunRecorder, RunRef
from worker.jobs.loading import LoadOutcome, TargetJob, load_target
from worker.jobs.model_selection import ResolvedModel, resolve_model
from worker.jobs.publish import PublishPlan, publish
from worker.jobs.publishing import InlineTarget, inline_targets
from worker.jobs.repo_config_load import PermanentTargetError, load_repo_config
from worker.jobs.slots import SLOT_WAIT_FOREVER, SlotGate, SlotUnavailable

__all__ = ["PermanentTargetError", "WorkerCtx", "review_target"]

_LOG = logging.getLogger("worker.review_target")

#: The registry role that runs V1.1's single-pass review as one ``sub`` run
#: (spec v2 §3, §15). V1.2 replaces it with the aspect sub-agents.
_REVIEWER_SPEC: AgentSpec = agent_spec("reviewer")

#: Position of each level in the tree: main(0) -> pr(1) -> sub(2) (spec §2).
_MAIN_DEPTH = 0
_PR_DEPTH = 1
_SUB_DEPTH = 2

#: Stand-in for a reviewer turn that produced no text at all (every model call
#: failed). The review outcome is unchanged — no findings — but the node's message
#: event still says why, instead of the node looking like it never spoke.
_NO_OUTPUT_NOTE = "no model output for this review pass"


class WorkerCtx(TypedDict):
    """The ARQ job context assembled by ``WorkerSettings`` startup hooks."""

    review: ReviewContext
    session_factory: SessionFactory
    job_try: NotRequired[int]
    #: The per-repo/per-installation slot gate. Absent means no caps are enforced
    #: at all — the shape every caller had before spec 10.5 existed.
    slots: NotRequired[SlotGate]


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


@dataclass(frozen=True, slots=True)
class _AttemptTree:
    """One attempt's run-tree nodes and the writer for their events.

    ``pr`` is the target's PR orchestrator run — reused by a retried attempt
    (spec §15) — and ``recorder`` writes both its events and the attempt's.
    """

    pr: RunRef
    recorder: RunRecorder


@dataclass(slots=True)
class _TurnOutcome:
    """What the reviewer's single turn produced, or the error that stopped it."""

    result: ReviewResult | None = None
    error: Exception | None = None


class _ReviewStopped(RuntimeError):
    """The reviewer run ended without a review (run ceiling or model resolution)."""


async def review_target(ctx: WorkerCtx, session_id: str, target_id: str) -> None:
    """Run one review target end to end, retrying transient failures via ARQ."""
    async with ctx["session_factory"]() as db:
        job = await _guarded_target(db, session_id, target_id)
        if job is None:
            return
        slots = ctx.get("slots")
        if slots is None:
            await _start_and_run(ctx=ctx, db=db, job=job)
            return
        try:
            async with slots.hold(job, wait_s=_slot_wait(ctx)) as waited:
                if waited:
                    # The target can sit here for a while, so re-read the guards
                    # before running it: one cancelled while it waited must not
                    # be reviewed anyway (spec §11).
                    db.expire_all()
                    reloaded = await _guarded_target(db, session_id, target_id)
                    if reloaded is None:
                        return
                    job = reloaded
                await _start_and_run(ctx=ctx, db=db, job=job)
        except SlotUnavailable as exc:
            raise _defer_without_slot(ctx=ctx, job=job) from exc


async def _guarded_target(
    db: AsyncSession, session_id: str, target_id: str
) -> TargetJob | None:
    """Load the target and apply the terminal and cancellation guards.

    Returns the job to run, or ``None`` when there is nothing to do — which
    includes having just recorded a cancelled session's target as cancelled.
    """
    load = await load_target(db, session_id, target_id)
    if load.outcome is LoadOutcome.MISSING:
        _LOG.warning(
            "target not found",
            extra={"session_id": session_id, "target_id": target_id},
        )
        return None
    if load.outcome is LoadOutcome.SESSION_CANCELLED and load.job is not None:
        await _cancel_target(db, load.job)
        await db.commit()
        return None
    if load.outcome in (LoadOutcome.TARGET_TERMINAL, LoadOutcome.SESSION_TERMINAL):
        return None
    return load.job


async def _start_and_run(*, ctx: WorkerCtx, db: AsyncSession, job: TargetJob) -> None:
    """Open this attempt's run row and execute it."""
    run = await persistence.start_run(db, job.target, now=_now())
    await db.commit()
    await _run_attempt(ctx=ctx, db=db, review=ctx["review"], job=job, run=run)


def _slot_wait(ctx: WorkerCtx) -> float:
    """Return how long this attempt may wait in-job for a free slot.

    ARQ counts every deferral against ``max_tries``, so a target deferred from
    its *last* permitted try would be dropped by the queue with the target still
    ``running`` — worse than a parked worker process. That attempt therefore
    waits out the job timeout instead of deferring, which keeps the gate from
    being what ends a target (spec 10.5).
    """
    config = ctx["review"].config
    if int(ctx.get("job_try", 1)) >= config.max_tries:
        return SLOT_WAIT_FOREVER
    return config.slot_wait_s


def _defer_without_slot(*, ctx: WorkerCtx, job: TargetJob) -> Retry:
    """Send a target that found no free slot back to the queue, unfailed.

    Nothing is written about the target or its attempts, so the deferral is
    invisible the next time this job runs: the gate decides when a target runs,
    and the queue decides whether it does (spec 10.5).
    """
    config = ctx["review"].config
    _LOG.info(
        "no free slot; deferring target",
        extra={
            "session_id": str(job.session_id),
            "target_id": str(job.target_id),
            "repo": job.repo_full_name,
            "waited_s": config.slot_wait_s,
        },
    )
    return Retry(defer=config.slot_defer_s)


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
    tree = await _open_tree(db, job, restart=True)
    # The tree is an artifact of its own: commit it before the attempt does any
    # work, so a failure below cannot erase the nodes a retry will reuse (§15).
    await db.commit()
    started = time.monotonic()
    try:
        plan = await _execute(db=db, review=review, job=job, tree=tree)
        await _record_review(tree=tree, job=job, result=plan.result)
        # Each run's row and its events become visible together, and before the
        # attempt's own writes, so no node reports a status without its log (§8).
        await db.commit()
    except PermanentTargetError as exc:
        await _fail_permanently(db=db, job=job, run=run, ids=ids, tree=tree, exc=exc)
        return
    except Exception as exc:  # classified as retryable or terminal below
        await _fail_retryable(ctx=ctx, db=db, job=job, run=run, ids=ids, tree=tree, exc=exc)
        return

    await _persist_and_publish(db=db, job=job, run=run, ids=ids, plan=plan)
    duration_ms = int((time.monotonic() - started) * 1000)
    await persistence.finish_run(
        db, target=job.target, run=run, now=_now(), duration_ms=duration_ms
    )
    await tree.recorder.finish(
        tree.pr.id,
        AgentStatus.DONE,
        summary=_review_summary(job, plan.result),
        finding_count=len(plan.result.findings),
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
    *, db: AsyncSession, review: ReviewContext, job: TargetJob, tree: _AttemptTree
) -> _AttemptPlan:
    """Resolve the model, re-read the PR, load repo config, and run the reviewer."""
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
    result = await _run_reviewer(
        db=db,
        job=job,
        review=review,
        clients=clients,
        tree=tree,
        model=model,
        repo_config=repo_config,
    )
    return _AttemptPlan(clients=clients, result=result, pull=pull, repo_config=repo_config)


async def _run_reviewer(
    *,
    db: AsyncSession,
    job: TargetJob,
    review: ReviewContext,
    clients: InstallationClients,
    tree: _AttemptTree,
    model: ResolvedModel,
    repo_config: RepoConfig,
) -> ReviewResult:
    """Run this attempt's review as one ``sub`` run of the target's PR run (§15).

    The review itself is untouched — one ``ReviewHarness`` call — but it is
    performed as the reviewer agent's single model turn, so the run tree gets a
    real node with its own steps, findings, and terminal event. The turn returns
    the harness's raw text and usage, which is what the runtime records.
    """
    outcome = _TurnOutcome()
    runtime = AgentRuntime(
        llm=_ReviewTurn(
            harness=review.build_harness(clients.reader),
            model=model,
            job=job,
            repo_config=repo_config,
            outcome=outcome,
        ),
        store=tree.recorder.store,
        bus=tree.recorder.bus,
        policy=Policy(),
        resolver=StaticResolver(
            {
                _REVIEWER_SPEC.model_role: ModelChoice(
                    model_id=model.model_id, provider=model.provider
                )
            }
        ),
    )
    agent = await runtime.run(
        _REVIEWER_SPEC,
        task=_reviewer_objective(job),
        session_id=job.session_id,
        target_id=job.target_id,
        parent_run_id=tree.pr.id,
        depth=_SUB_DEPTH,
    )
    # The run the reviewer just produced is durable on its own: commit it before
    # anything below can fail, so an attempt that fails still leaves its node —
    # and the events that explain it — in the tree for the retry to sit beside.
    await db.commit()
    if outcome.result is None:
        # Nothing to persist: the reviewer never completed a pass, so the attempt
        # fails the way it would have before the tree existed, and retries.
        raise outcome.error if outcome.error is not None else _ReviewStopped(str(agent.status))
    return outcome.result


class _ReviewTurn:
    """``LlmTurn`` whose single turn is the existing single-pass review (spec §15).

    V1.1 has no model loop inside a reviewer: the harness composes its own prompt
    and makes its own calls, so the turn ignores the transcript and tools it is
    handed and reports what the harness returned as one assistant turn.
    """

    def __init__(
        self,
        *,
        harness: ReviewHarness,
        model: ResolvedModel,
        job: TargetJob,
        repo_config: RepoConfig,
        outcome: _TurnOutcome,
    ) -> None:
        self._harness = harness
        self._model = model
        self._job = job
        self._repo_config = repo_config
        self._outcome = outcome

    async def complete(
        self, *, model_id: str, messages: list[Message], tools: list[ToolSpec]
    ) -> AssistantTurn:
        """Review the PR and return its raw output plus the tokens it cost."""
        try:
            result = await self._harness.review(
                repo_full_name=self._job.repo_full_name,
                number=self._job.target.number,
                session_prompt=self._job.session.prompt,
                repo_config=self._repo_config,
                model=self._model.model_id,
                provider=self._model.provider,
            )
        except Exception as exc:
            # Recorded so the caller can fail the attempt exactly as before, then
            # re-raised so the runtime marks this run failed and emits its event.
            self._outcome.error = exc
            raise
        self._outcome.result = result
        return AssistantTurn(
            text=result.raw if result.raw is not None else _NO_OUTPUT_NOTE,
            tokens=result.tokens,
            cost_usd=result.cost_usd,
        )


async def _record_review(*, tree: _AttemptTree, job: TargetJob, result: ReviewResult) -> None:
    """Log the PR node's delegated review: one step, its summary, its findings.

    The PR orchestrator does not call a model in V1.1 — it delegates — so the node
    narrates the harness turn its reviewer performed rather than claiming one.
    """
    recorder = tree.recorder
    summary = _review_summary(job, result)
    await recorder.emit(
        tree.pr.id,
        EventType.STEP,
        {
            "step": 1,
            "summary": f"delegated the review of PR #{job.target.number} to one reviewer",
            "tool_call_count": 1,
        },
    )
    await recorder.emit(
        tree.pr.id,
        EventType.MESSAGE,
        {"role": "assistant", "summary": summary, "chars": len(summary)},
    )
    for finding in result.findings:
        await recorder.emit(tree.pr.id, EventType.FINDING, _finding_payload(finding))


async def _open_tree(db: AsyncSession, job: TargetJob, *, restart: bool) -> _AttemptTree:
    """Find or create this attempt's tree nodes and announce them (spec §15).

    Both nodes are created lazily and idempotently: the submit path creates the
    session's main run, and a session submitted before it existed still gets a
    root, while the target's PR run is the job's own and survives a retry
    unchanged. ``restart`` says the caller is beginning a new attempt — only then
    does a PR run that already exists log another start — so a cancellation sweep
    closes nodes without opening them again.
    """
    recorder = RunRecorder(db)
    store = recorder.store
    main = await store.find_or_create_main_run(job.session)
    await recorder.open_run(main, depth=_MAIN_DEPTH)
    pr = await store.find_or_create_pr_run(job.session, job.target, main.id)
    if pr.created:
        await recorder.open_run(pr, depth=_PR_DEPTH)
    elif restart:
        # A retried attempt reuses the target's PR run: the node starts again, but
        # it is never spawned twice (spec §15).
        await recorder.start_run(pr)
    return _AttemptTree(pr=pr, recorder=recorder)


async def _cancel_target(db: AsyncSession, job: TargetJob) -> None:
    """Cancel a cancelled session's target and the run nodes it still owns (§11)."""
    tree = await _open_tree(db, job, restart=False)
    await tree.recorder.cancel_target_runs(
        job.target_id,
        summary=f"cancelled with its session before {job.repo_full_name}"
        f"#{job.target.number} was reviewed",
    )
    await persistence.mark_target_cancelled(db, job.target)
    await persistence.recompute_session(db, job.session_id)


def _review_summary(job: TargetJob, result: ReviewResult) -> str:
    """One readable line about a completed pass, plus whatever it could not do."""
    line = (
        f"PR #{job.target.number}: {len(result.findings)} finding(s), "
        f"{result.tokens} tokens, ${result.cost_usd:.4f}"
    )
    return "\n".join([line, *result.notes]) if result.notes else line


def _reviewer_objective(job: TargetJob) -> str:
    """The PR-scoped objective handed to the reviewer sub-agent (spec §4)."""
    return f"Review pull request {job.repo_full_name}#{job.target.number}: {job.target.title}"


def _finding_payload(finding: CoreFinding) -> dict[str, object]:
    """Build the allow-listed payload for one ``agent.finding`` event (spec §7)."""
    return {
        "path": finding.path,
        "line": finding.line,
        "severity": finding.severity.value,
        "category": finding.category,
        "message": finding.message,
        "suggestion": finding.suggestion,
        "confidence": finding.confidence,
    }


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
    tree: _AttemptTree,
    exc: Exception,
) -> None:
    """Mark the target failed without retrying (e.g. invalid config)."""
    await db.rollback()
    error = f"{type(exc).__name__}: {exc}"
    await persistence.fail_target(db, target=job.target, run=run, error=error, now=_now())
    await tree.recorder.finish(tree.pr.id, AgentStatus.FAILED, summary=error, error=error)
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
    tree: _AttemptTree,
    exc: Exception,
) -> None:
    """Record the failed attempt and retry with backoff, or fail the target."""
    await db.rollback()
    config = ctx["review"].config
    attempt = int(ctx.get("job_try", 1))
    error = f"{type(exc).__name__}: {exc}"
    # The PR run follows the target: it stays reusable while the target is still
    # running, and its node records the failed attempt either way (§11).
    await tree.recorder.finish(tree.pr.id, AgentStatus.FAILED, summary=error, error=error)
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
