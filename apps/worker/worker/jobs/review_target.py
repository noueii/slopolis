"""ARQ job: run one review target and publish its results (spec 10.5-10.9).

`review_target(ctx, session_id, target_id, mode)` is the unit of work the server
enqueues once per PR target. It loads the target graph, guards on terminal and
cancelled state, opens the target's nodes in the Harness V1.1 run tree (the
session's `main` run and this target's `pr` run, then the reviewer's `sub` run),
runs the single-pass harness, persists findings and usage, publishes to GitHub,
and recomputes the parent session. The review's output is committed before
anything is posted, so a refused or unreachable publish leaves the findings
visible rather than erasing the run's expensive half. Transient failures retry
with bounded exponential backoff; permanent failures (invalid repo config, an
installation GitHub refused to authorize) fail the target immediately. One
target's failure never affects another — they are separate jobs.

`mode` says what the attempt is for (spec 10.5 §Retrying a run that only failed
to publish). `"review"` — what a submission and an ordinary retry ask for — runs
the harness. `"publish"` is a retry of a target whose last attempt failed after
the model had run: its review is already persisted, unposted, so this attempt
rebuilds the publish payloads from those findings and posts them, with no model
call and no second bill for the same answer. The server decided it where the
target's state is known and passed it with the job, so this job never guesses.

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
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from slopolis_core.config.repo_config import RepoConfig
from slopolis_core.domain import Severity, TargetStatus
from slopolis_core.findings import Finding as CoreFinding
from slopolis_core.github.errors import GitHubAuthError
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
from slopolis_core.llm.client import LlmClient
from slopolis_core.review.harness import ReviewHarness, ReviewResult
from slopolis_core.settings import get_settings
from slopolis_db.models import Finding, SessionTargetRun
from worker.deps import (
    InstallationClients,
    InstallationRef,
    ReviewContext,
    SessionFactory,
)
from worker.errors import PermanentTargetError, UnknownModeError
from worker.jobs import persistence
from worker.jobs.agent_runs import RunRecorder, RunRef
from worker.jobs.loading import LoadOutcome, TargetJob, load_target
from worker.jobs.model_selection import ResolvedModel, resolve_model
from worker.jobs.publish import InlinePost, PublishPlan, publish
from worker.jobs.publishing import InlineTarget, inline_targets
from worker.jobs.repo_config_load import load_repo_config
from worker.jobs.slots import SLOT_WAIT_FOREVER, SlotGate, SlotUnavailable

__all__ = ["WorkerCtx", "review_target"]

_LOG = logging.getLogger("worker.review_target")

#: The two things a retry can ask this job for (spec 10.5 §Retrying a run that
#: only failed to publish). ``PUBLISH`` re-posts the review the last attempt
#: already bought; ``REVIEW`` runs the model again. The server decides, and the
#: job is told — this module never re-derives the decision.
REVIEW = "review"
PUBLISH = "publish"

#: Every mode this build accepts, the two constants above and nothing else. A job
#: that arrives with any other value came from a server that disagrees with this
#: worker about what the job is for, and the disagreement has to be named: treating
#: it as a review would spend a model call nobody asked for, and letting it fall
#: through to a signature that no longer takes the parameter reports a bare
#: ``TypeError`` naming neither the mode nor the target.
_MODES = (REVIEW, PUBLISH)

#: The registry role that runs V1.1's single-pass review as one ``sub`` run
#: (spec v2 §3, §15). V1.2 replaces it with the aspect sub-agents.
_REVIEWER_SPEC: AgentSpec = agent_spec("reviewer")

#: What the run tree says when a retry re-posts a review instead of buying it
#: again. The node is what the history reads, so it says what happened.
_PUBLISH_RETRY_MESSAGE = "re-published the existing review without a model call"

#: How the attempt's own text — its node summary, or its stored error when it
#: failed — marks itself. Without it, a publish retry that spends nothing reads
#: exactly like a review that cost nothing (spec 10.5).
_PUBLISH_RETRY_MARK = "publish retry: the review was not re-run"

#: What a publish retry says when the attempt that bought the review is gone.
#: Publishing an empty review instead would be a silent lie; there is nothing to
#: re-derive it from, and no retry can conjure it back.
_NO_REVIEW_TO_PUBLISH = "the attempt that bought this review is no longer recorded"

#: Position of each level in the tree: main(0) -> pr(1) -> sub(2) (spec §2).
_MAIN_DEPTH = 0
_PR_DEPTH = 1
_SUB_DEPTH = 2

#: Stand-in for a reviewer turn that produced no text at all (every model call
#: failed). The review outcome is unchanged — no findings — but the node's message
#: event still says why, instead of the node looking like it never spoke.
_NO_OUTPUT_NOTE = "no model output for this review pass"

#: Appended to a 403 refusal. Retrying cannot fix a permission the App was never
#: granted, and the operator reading the failed target is the only one who can
#: grant it, so the stored error names the scope and where to apply it (§10.7).
_PERMISSION_HINT = (
    "the GitHub App installation cannot write: posting the review needs Pull requests "
    "'Read & write', and the check run needs Checks 'Read & write' — grant them on the App, "
    "then approve the update for the installation"
)


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


async def review_target(
    ctx: WorkerCtx, session_id: str, target_id: str, mode: str = REVIEW
) -> None:
    """Run one review target end to end, retrying transient failures via ARQ.

    ``mode`` is the server's decision for this attempt (spec 10.5): ``"review"``
    or ``"publish"``. A retried job keeps whatever the queue was given — an
    attempt that fails and is retried by ARQ runs the same mode again, which is
    what keeps a throttled publish from turning into a second review. A mode this
    build does not define fails the job before it touches the target, naming the
    mode and the target it arrived on.
    """
    if mode not in _MODES:
        raise UnknownModeError(
            f"review_target does not know mode {mode!r} for target {target_id}; "
            f"expected one of {', '.join(_MODES)}"
        )
    async with ctx["session_factory"]() as db:
        job = await _guarded_target(db, session_id, target_id)
        if job is None:
            return
        slots = ctx.get("slots")
        if slots is None:
            await _start_and_run(ctx=ctx, db=db, job=job, mode=mode)
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
                await _start_and_run(ctx=ctx, db=db, job=job, mode=mode)
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


async def _start_and_run(
    *, ctx: WorkerCtx, db: AsyncSession, job: TargetJob, mode: str
) -> None:
    """Open this attempt's run row and execute it."""
    run = await persistence.start_run(db, job.target, now=_now())
    await db.commit()
    await _run_attempt(ctx=ctx, db=db, review=ctx["review"], job=job, run=run, mode=mode)


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
    mode: str,
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
    if mode == REVIEW:
        # A new review supersedes the previous one's findings, whichever path
        # asked for it — an automatic retry after a throttled publish, or a manual
        # retry (spec 10.5). Discarding them here rather than on the failure path
        # keeps them visible while a retry waits, and keeps one rule in one place:
        # a posted finding is the app's link to a comment that is on the pull
        # request, so it is never dropped. The first attempt finds nothing to
        # discard.
        #
        # A publish retry is the one attempt that keeps them: those unposted rows
        # are the review it was asked to post, and deleting them would publish an
        # empty summary instead (spec 10.5 §Retrying a run that only failed to
        # publish).
        discarded = await persistence.discard_unposted_findings(db, job.target_id)
        if discarded:
            _LOG.info(
                "superseded unposted findings",
                extra={"target_id": str(job.target_id), "count": discarded},
            )
        await db.commit()
    started = time.monotonic()
    try:
        if mode == PUBLISH:
            # Publishing is inside the guarded region for the same reason it is in
            # review mode: a refused or throttled write is an attempt failure like
            # any other and must close the attempt row (§10.7).
            plan, check_note = await _publish_attempt(
                db=db, review=review, job=job, run=run, tree=tree, ids=ids
            )
        else:
            plan, check_note = await _review_attempt(
                db=db, review=review, job=job, run=run, tree=tree, ids=ids
            )
    except PermanentTargetError as exc:
        await _fail_permanently(
            db=db, job=job, run=run, ids=ids, tree=tree, exc=exc, mode=mode
        )
        return
    except GitHubAuthError as exc:
        # A missing App permission or a revoked token is not a transient state:
        # waiting cannot grant it, so the target fails now instead of paying for
        # the same review again and being refused the same way (§10.7). The
        # attempt's findings are already committed and stay visible.
        # ``GitHubRateLimitError`` is a sibling of this error rather than a
        # subclass, so a throttled 403 still reaches the retry handler below.
        await _fail_permanently(
            db=db, job=job, run=run, ids=ids, tree=tree, exc=exc, mode=mode
        )
        return
    except Exception as exc:  # classified as retryable or terminal below
        await _fail_retryable(
            ctx=ctx, db=db, job=job, run=run, ids=ids, tree=tree, exc=exc, mode=mode
        )
        return

    duration_ms = int((time.monotonic() - started) * 1000)
    await persistence.finish_run(
        db, target=job.target, run=run, now=_now(), duration_ms=duration_ms
    )
    if check_note is not None:
        # The review is published and the target is done, so a warning is the only
        # place left to say the check run is missing from the pull request (§10.7).
        _LOG.warning(
            "check run skipped; the published review stands",
            extra={
                "session_id": str(ids.session),
                "target_id": str(ids.target),
                "reason": check_note,
            },
        )
    await tree.recorder.finish(
        tree.pr.id,
        AgentStatus.DONE,
        summary=_attempt_text(
            _review_summary(job, plan.result, skipped=check_note), mode
        ),
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


async def _review_attempt(
    *,
    db: AsyncSession,
    review: ReviewContext,
    job: TargetJob,
    run: SessionTargetRun,
    tree: _AttemptTree,
    ids: _Ids,
) -> tuple[_AttemptPlan, str | None]:
    """Review the pull request, persist its output, and publish it.

    Returns the plan it published and the note about a surface GitHub refused, so
    the success tail can finish the attempt exactly as before.
    """
    plan = await _execute(db=db, review=review, job=job, tree=tree)
    await _record_review(tree=tree, job=job, result=plan.result)
    # Each run's row and its events become visible together, and before the
    # attempt's own writes, so no node reports a status without its log (§8).
    await db.commit()
    # The check run is best effort — the publisher records its refusal instead of
    # raising, and returns why (see ``_persist_and_publish``).
    check_note = await _persist_and_publish(db=db, job=job, run=run, ids=ids, plan=plan)
    return plan, check_note


async def _publish_attempt(
    *,
    db: AsyncSession,
    review: ReviewContext,
    job: TargetJob,
    run: SessionTargetRun,
    tree: _AttemptTree,
    ids: _Ids,
) -> tuple[_AttemptPlan, str | None]:
    """Re-post the review this target's last attempt already bought (spec 10.5).

    No model is resolved and none is called: the review exists as persisted
    findings, so this attempt only re-reads the pull request (for the head commit
    the inline comments anchor to) and the repository config (which decides what
    to post), rebuilds the payloads the original publish would have sent, and
    posts them. The attempt is recorded like any other, marked as a publish
    retry, and any refusal behaves exactly as it would have in the attempt that
    first tried to publish.
    """
    review_attempt = await _last_attempt(db, target_id=job.target_id, current=run)
    if review_attempt is None:
        raise PermanentTargetError(_NO_REVIEW_TO_PUBLISH)
    clients = await review.build_clients(InstallationRef(job.installation.installation_id))
    pull = await clients.reader.get_pull_request(job.repo_full_name, job.target.number)
    repo_config = await load_repo_config(
        clients.reader, job.repo_full_name, pull.head_sha, review.config
    )
    rows = await _unposted_findings(db, job.target_id)
    plan = _AttemptPlan(
        clients=clients,
        result=_rebuilt_review(job, attempt=review_attempt, rows=rows),
        pull=pull,
        repo_config=repo_config,
    )
    job.target.head_branch = plan.pull.head_branch
    # ``reviewed_sha`` is deliberately left where the review attempt put it: the
    # head read above is the commit the pull request sits at *now*, which is the
    # commit this retry anchors its comments to, not the one the findings it is
    # posting came from. Writing it here would report a stale review as current
    # the moment someone pushed (spec v3 §2).
    inline = inline_targets(
        plan.result.findings,
        threshold=plan.repo_config.review.severity_threshold,
        suggestions=plan.repo_config.output.suggestions,
    )
    # The spend belongs to the attempt that bought it: this attempt copies the
    # review's usage onto its own row, so a publish retry that fails again is
    # still read as one (the server's rule asks for recorded tokens, spec 10.5),
    # but records no usage — the model was never called, and a second usage row
    # would bill the same review twice.
    persistence.record_run_usage(job.target, run, plan.result)
    await _record_republish(tree=tree)
    # Everything above is this attempt's own writing and must be durable before
    # GitHub is asked to change the pull request (spec 10.7).
    await db.commit()
    check_note = await _publish_review(
        db=db, job=job, ids=ids, plan=plan, inline=inline, rows=rows
    )
    return plan, check_note


async def _last_attempt(
    db: AsyncSession, *, target_id: uuid.UUID, current: SessionTargetRun
) -> SessionTargetRun | None:
    """The attempt before this one: the row whose review is being re-published.

    A publish retry opens its own attempt row before it gets here, so the review
    belongs to the newest row that is not it — the failed attempt whose recorded
    tokens are what the server read when it decided this target was a publish
    retry (spec 10.5).
    """
    return await db.scalar(
        select(SessionTargetRun)
        .where(
            SessionTargetRun.target_id == target_id,
            SessionTargetRun.id != current.id,
        )
        .order_by(SessionTargetRun.attempt.desc())
        .limit(1)
    )


async def _unposted_findings(db: AsyncSession, target_id: uuid.UUID) -> list[Finding]:
    """The review's persisted findings, as the rows this attempt publishes.

    A review attempt discards the previous attempt's unposted rows before it
    persists its own (see ``_run_attempt``), and a publish attempt persists none,
    so a target's unposted rows are exactly the last review's findings — whatever
    attempt row they were filed under. Reading them that way is also what lets a
    second publish retry post the same review instead of an empty summary.

    The rows carry no position of their own, so what the order guarantees is not
    the model's original order but that this same list becomes the rebuilt
    ``ReviewResult``'s findings and the rows the inline comments are stamped on:
    every comment is stamped on the finding it renders.
    """
    rows = await db.scalars(
        select(Finding)
        .where(Finding.target_id == target_id, Finding.posted.is_(False))
        .order_by(Finding.created_at, Finding.id)
    )
    return list(rows.all())


def _rebuilt_review(
    job: TargetJob, *, attempt: SessionTargetRun, rows: list[Finding]
) -> ReviewResult:
    """Rebuild the ``ReviewResult`` the persisted findings were published from.

    Only what publishing reads is recovered: the findings, the usage the attempt
    recorded, and the session's model and provider (the shape ``ReviewResult``
    takes). A review's notes are not stored anywhere, so a pass that carried one
    is re-published without it — the findings are the review itself.
    """
    return ReviewResult(
        findings=[_core_finding(row) for row in rows],
        model=job.session.model,
        provider=job.session.provider,
        tokens=attempt.tokens,
        cost_usd=float(attempt.cost_usd),
    )


def _core_finding(row: Finding) -> CoreFinding:
    """Map a persisted finding row back onto the harness's finding model."""
    return CoreFinding(
        path=row.path,
        line=row.line,
        severity=Severity(row.severity),
        category=row.category,
        message=row.message,
        suggestion=row.suggestion,
        confidence=row.confidence,
    )


async def _record_republish(*, tree: _AttemptTree) -> None:
    """Log a publish retry on the PR node: what happened, without model events.

    Only the message: the previous attempt's findings and step are already on this
    node, and the node is reused, so repeating them would read as a second review
    that produced the same findings again.
    """
    await tree.recorder.emit(
        tree.pr.id,
        EventType.MESSAGE,
        {
            "role": "assistant",
            "summary": _PUBLISH_RETRY_MESSAGE,
            "chars": len(_PUBLISH_RETRY_MESSAGE),
        },
    )


async def _execute(
    *, db: AsyncSession, review: ReviewContext, job: TargetJob, tree: _AttemptTree
) -> _AttemptPlan:
    """Resolve the model client, re-read the PR, load repo config, and review."""
    model = await resolve_model(
        db,
        workspace_id=job.workspace.id,
        fallback_model=job.session.model,
        fallback_provider=job.session.provider,
    )
    # Before any GitHub work: a model neither a credential nor the gateway can
    # serve is a configuration failure, and it should not cost a PR read — or a
    # retry — to discover that (spec 10.2).
    llm = review.client_for(model_id=model.model_id, credential=model.credential)
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
        llm=llm,
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
    llm: LlmClient,
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
            harness=review.build_harness(clients.reader, llm),
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
    if main.created:
        await recorder.open_run(main, depth=_MAIN_DEPTH)
    elif restart:
        # A retried session reuses its main run: its root starts again, which is
        # also what clears the terminal status the failed attempt left on it (a
        # terminal root is never finished again, see ``finish_main_run``). A root
        # that is still running — another target's attempt — is left alone.
        await recorder.restart_run(main)
    pr = await store.find_or_create_pr_run(job.session, job.target, main.id)
    if pr.created:
        await recorder.open_run(pr, depth=_PR_DEPTH)
    elif restart:
        # A retried attempt reuses the target's PR run: the node starts again, but
        # it is never spawned twice (spec §15).
        await recorder.restart_run(pr)
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


def _review_summary(job: TargetJob, result: ReviewResult, *, skipped: str | None = None) -> str:
    """One readable line about a completed pass, plus whatever it could not do.

    ``skipped`` is the note about a surface GitHub refused while the review
    published anyway (spec 10.7). It lands in the node's summary because nothing
    failed: the tree is where a reader learns the check run is missing.
    """
    line = (
        f"PR #{job.target.number}: {len(result.findings)} finding(s), "
        f"{result.tokens} tokens, ${result.cost_usd:.4f}"
    )
    notes = [*result.notes, skipped] if skipped is not None else result.notes
    return "\n".join([line, *notes]) if notes else line


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
) -> str | None:
    """Commit the review's output, then publish it to GitHub.

    The findings and the usage they cost are the expensive half of an attempt and
    are committed *before* anything is posted: a publish GitHub refuses or never
    answers then leaves them visible in the app, and the attempt's failure writes
    (which begin with a rollback) cannot undo a commit. Posting is the last thing
    an attempt does, and the comment ids it returns are stamped on the rows that
    are already durable (spec 10.7).

    Returns the note about a check run GitHub refused, or ``None`` when every
    surface the config asked for posted. The caller folds it into the node's
    summary, so a skipped check run is told apart from a publish that never
    happened.

    Only ``_review_attempt`` reaches this function, which is what makes it the
    one place a target's ``reviewed_sha`` may be written (spec v3 §2): a publish
    retry posts a review that already exists and only re-reads the pull request
    for a head to anchor its comments to, so a write from there would claim the
    review covered commits it never read (see ``_publish_attempt``).
    """
    job.target.head_branch = plan.pull.head_branch
    # The commit this review was run against, recorded in the same commit as the
    # findings that came from it: an attempt that never produced a review fails
    # before this commit and rolls back, so this column only ever names a review
    # the app can read, and it names the one the row above persists (spec v3 §2).
    job.target.reviewed_sha = plan.pull.head_sha
    inline = inline_targets(
        plan.result.findings,
        threshold=plan.repo_config.review.severity_threshold,
        suggestions=plan.repo_config.output.suggestions,
    )
    rows = await persistence.persist_findings(
        db, target_id=ids.target, run_id=ids.run, findings=plan.result.findings
    )
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
    await db.commit()
    return await _publish_review(db=db, job=job, ids=ids, plan=plan, inline=inline, rows=rows)


async def _publish_review(
    *,
    db: AsyncSession,
    job: TargetJob,
    ids: _Ids,
    plan: _AttemptPlan,
    inline: list[InlineTarget],
    rows: list[Finding],
) -> str | None:
    """Post one attempt's summary, inline comments, and check run; stamp what posted.

    Shared by both modes so a publish retry sends exactly what the attempt that
    first tried to publish would have sent — same repo config toggles, same
    payloads, same best-effort check run.
    """

    async def stamp(post: InlinePost) -> None:
        """Record one finding's comment the moment that comment exists (spec 10.7).

        The commit is the point of it: the failure path begins with a rollback,
        so an uncommitted link would be rolled back too and the app would show a
        finding as unposted while its comment sat on the pull request — which is
        how a retry came to post the same review twice.
        """
        row = rows[post.source_index]
        row.posted = True
        row.github_comment_id = post.comment_id
        await db.commit()

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
        stamp=stamp,
    )
    if outcome.check_run_skipped is None:
        return None
    return f"the review posted without a check run: {outcome.check_run_skipped}"


def _error_text(exc: Exception) -> str:
    """Format one attempt failure for storage, with the advice a 403 needs.

    A 403 is a permission refusal — the installed App lacks the write scope — and
    a 401 is bad or expired credentials; both arrive as
    :class:`GitHubAuthError`, discriminated by the status the client recorded in
    its message. Only the permission case gets instructions, because only it asks
    the operator to change something (spec 10.7).
    """
    text = f"{type(exc).__name__}: {exc}"
    if isinstance(exc, GitHubAuthError) and "403" in str(exc):
        return f"{text} — {_PERMISSION_HINT}"
    return text


def _attempt_text(text: str, mode: str) -> str:
    """Mark a publish retry's own history text, so the attempt explains itself.

    The attempt's summary — or its stored error, when it failed — is where a
    reader learns what happened. A publish retry is the one attempt that produced
    nothing new, so it says so instead of looking like a review that spent nothing
    (spec 10.5).
    """
    return f"{_PUBLISH_RETRY_MARK}\n{text}" if mode == PUBLISH else text


async def _fail_permanently(
    *,
    db: AsyncSession,
    job: TargetJob,
    run: SessionTargetRun,
    ids: _Ids,
    tree: _AttemptTree,
    exc: Exception,
    mode: str,
) -> None:
    """Mark the target failed without retrying (e.g. invalid config, no App permission)."""
    await db.rollback()
    error = _attempt_text(_error_text(exc), mode)
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
    mode: str,
) -> None:
    """Record the failed attempt and retry with backoff, or fail the target."""
    await db.rollback()
    config = ctx["review"].config
    attempt = int(ctx.get("job_try", 1))
    error = _attempt_text(_error_text(exc), mode)
    # The PR run follows the target: it stays reusable while the target is still
    # running, and its node records the failed attempt either way (§11).
    await tree.recorder.finish(tree.pr.id, AgentStatus.FAILED, summary=error, error=error)
    if attempt < config.max_tries:
        # This attempt's findings are already committed (§10.7) and stay visible
        # until the next attempt starts and supersedes them (see ``_run_attempt``)
        # — dropping them here would hide a review while its retry waits in
        # backoff, for no gain: the retry discards them the moment it begins.
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
