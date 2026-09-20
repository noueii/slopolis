"""Run-tree write path for the Harness V1.1 supervisor tree (spec v2 §7-§8, §15).

The worker owns three kinds of node: the session's ``main`` run, one ``pr`` run
per target, and one ``sub`` run per review attempt. ``DatabaseRunStore`` is the
``RunStore`` port the core runtime writes through, ``DatabaseEventSink`` is the
``EventSink`` the core event bus fans out to, and ``RunRecorder`` is what the job
calls directly for the nodes the worker itself opens and closes.

Sequencing and redaction stay in ``slopolis_core``: the sink stores whatever the
bus gives it. A bus counter starts at 1 in memory, though, so a retried attempt
would collide with the rows the previous attempt wrote on the run it reuses
(spec §15) — ``RunRecorder`` primes the counter from the persisted log first, and
``(run_id, seq)`` stays contiguous per run.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from slopolis_core.harness import (
    MAIN_AGENT,
    PR_AGENT,
    AgentEvent,
    AgentStatus,
    EventBus,
    EventSink,
    EventType,
    HarnessLevel,
    RunStore,
    agent_spec,
)
from slopolis_db.models import AgentEventRow, AgentRun, ReviewSession, SessionTarget

__all__ = [
    "DatabaseEventSink",
    "DatabaseRunStore",
    "RunRecorder",
    "RunRef",
    "finish_session_main_run",
]

#: Column cap for a run's error message, matching the target-attempt columns.
_ERROR_LIMIT = 2000

#: Statuses a run never leaves once written; a terminal run is not finished twice.
_TERMINAL_STATUSES = frozenset(
    {AgentStatus.DONE.value, AgentStatus.FAILED.value, AgentStatus.CANCELLED.value}
)

#: Objective recorded on a session's ``main`` run: V1.1 aggregates, it does not
#: call a model, so this is the whole of the node's work (spec §15).
_MAIN_OBJECTIVE = "Coordinate the review of every target in this session"


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


@dataclass(frozen=True, slots=True)
class RunRef:
    """One tree node's identity and row facts, as plain data.

    Deliberately not the ORM row: a retried attempt rolls back mid-job, and plain
    values survive that where an expired instance's attributes would not.
    """

    id: uuid.UUID
    created: bool
    role: str
    level: HarnessLevel
    objective: str
    parent_run_id: uuid.UUID | None
    target_id: uuid.UUID | None


class DatabaseRunStore(RunStore):
    """``agent_runs`` rows: the ``RunStore`` port plus the tree's find-or-create."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def create_run(
        self,
        *,
        session_id: uuid.UUID,
        target_id: uuid.UUID | None,
        parent_run_id: uuid.UUID | None,
        level: HarnessLevel,
        role: str,
        model_id: str | None,
        objective: str,
    ) -> uuid.UUID:
        """Insert one running run row and return its id (spec §5: the run comes first)."""
        run = AgentRun(
            session_id=session_id,
            target_id=target_id,
            parent_run_id=parent_run_id,
            level=level.value,
            role=role,
            model_id=model_id,
            objective=objective,
            status=AgentStatus.RUNNING.value,
            started_at=_now(),
        )
        self._db.add(run)
        await self._db.flush()
        return run.id

    async def finish_run(
        self,
        *,
        run_id: uuid.UUID,
        status: AgentStatus,
        tokens: int,
        cost_usd: float,
        error: str | None = None,
    ) -> None:
        """Stamp a run's terminal state (spec §8).

        A row that is already gone (its session was deleted, which cascades) has
        nothing left to stamp, so the write is skipped rather than raising inside
        a job that is otherwise finished.
        """
        run = await self._db.get(AgentRun, run_id)
        if run is None:
            return
        run.status = status.value
        run.tokens = tokens
        run.cost_usd = Decimal(str(cost_usd))
        run.ended_at = _now()
        run.error = None if error is None else error[:_ERROR_LIMIT]
        await self._db.flush()

    async def last_seq(self, run_id: uuid.UUID) -> int:
        """Highest persisted ``seq`` for a run, or 0 when it has no events yet."""
        highest = await self._db.scalar(
            select(func.max(AgentEventRow.seq)).where(AgentEventRow.run_id == run_id)
        )
        return int(highest or 0)

    async def find_or_create_main_run(self, session: ReviewSession) -> RunRef:
        """Return the session's single ``main`` run, creating it when absent.

        The submit path creates this row (spec §15); creating it lazily here too
        keeps a session submitted before that change from having no root node.
        """
        existing = await self._find(
            session_id=session.id, level=HarnessLevel.MAIN, target_id=None
        )
        if existing is not None:
            return existing
        run_id = await self.create_run(
            session_id=session.id,
            target_id=None,
            parent_run_id=None,
            level=HarnessLevel.MAIN,
            role=MAIN_AGENT,
            model_id=None,
            objective=_MAIN_OBJECTIVE,
        )
        return RunRef(
            id=run_id,
            created=True,
            role=MAIN_AGENT,
            level=HarnessLevel.MAIN,
            objective=_MAIN_OBJECTIVE,
            parent_run_id=None,
            target_id=None,
        )

    async def find_or_create_pr_run(
        self, session: ReviewSession, target: SessionTarget, main_run_id: uuid.UUID
    ) -> RunRef:
        """Return the target's single ``pr`` run, creating it when absent.

        A retried attempt reuses the row it already has — the runtime enforces 1:1
        through the partial unique index on ``(session_id, target_id)`` for
        ``level='pr'``, so a second insert for the same target is not an option
        (spec §2, §15). V1.1 resolves no model for this level: the PR orchestrator
        delegates rather than calls one, so ``model_id`` stays NULL.
        """
        existing = await self._find(
            session_id=session.id, level=HarnessLevel.PR, target_id=target.id
        )
        if existing is not None:
            return existing
        objective = _pr_objective(target)
        run_id = await self.create_run(
            session_id=session.id,
            target_id=target.id,
            parent_run_id=main_run_id,
            level=HarnessLevel.PR,
            role=PR_AGENT,
            model_id=None,
            objective=objective,
        )
        return RunRef(
            id=run_id,
            created=True,
            role=PR_AGENT,
            level=HarnessLevel.PR,
            objective=objective,
            parent_run_id=main_run_id,
            target_id=target.id,
        )

    async def finish_main_run(
        self, session_id: uuid.UUID, status: AgentStatus
    ) -> uuid.UUID | None:
        """Finish the session's ``main`` run, returning its id when this call did.

        Returns ``None`` when the session has no main run — a session that never
        ran must not gain one — or when its main run is already terminal, which
        keeps the root node's single terminal event single.
        """
        run = await self._find_entity(
            session_id=session_id, level=HarnessLevel.MAIN, target_id=None
        )
        if run is None or run.status in _TERMINAL_STATUSES:
            return None
        tokens, cost_usd = await self._session_totals(session_id)
        await self.finish_run(
            run_id=run.id, status=status, tokens=tokens, cost_usd=cost_usd
        )
        return run.id

    async def cancel_target_runs(self, target_id: uuid.UUID) -> list[uuid.UUID]:
        """Mark every non-terminal run of one target cancelled; return their ids.

        Cancellation propagates down the tree (spec §11): a job that finds its
        session cancelled cancels the nodes it still owns and leaves runs that
        already reached a terminal status alone.
        """
        runs = list(
            (
                await self._db.scalars(
                    select(AgentRun).where(
                        AgentRun.target_id == target_id,
                        AgentRun.status.not_in(_TERMINAL_STATUSES),
                    )
                )
            ).all()
        )
        for run in runs:
            run.status = AgentStatus.CANCELLED.value
            run.ended_at = _now()
        await self._db.flush()
        return [run.id for run in runs]

    async def _find(
        self,
        *,
        session_id: uuid.UUID,
        level: HarnessLevel,
        target_id: uuid.UUID | None,
    ) -> RunRef | None:
        """Look up a node as plain data, or None when it does not exist yet."""
        run = await self._find_entity(session_id=session_id, level=level, target_id=target_id)
        if run is None:
            return None
        return RunRef(
            id=run.id,
            created=False,
            role=run.role,
            level=HarnessLevel(run.level),
            objective=run.objective,
            parent_run_id=run.parent_run_id,
            target_id=run.target_id,
        )

    async def _find_entity(
        self,
        *,
        session_id: uuid.UUID,
        level: HarnessLevel,
        target_id: uuid.UUID | None,
    ) -> AgentRun | None:
        """Load the single node a (session, level, target) triple may have."""
        found = await self._db.scalar(
            select(AgentRun)
            .where(
                AgentRun.session_id == session_id,
                AgentRun.level == level.value,
                AgentRun.target_id == target_id,
            )
            .order_by(AgentRun.created_at)
            .limit(1)
        )
        return found

    async def _session_totals(self, session_id: uuid.UUID) -> tuple[int, float]:
        """Sum the session's target tokens and cost, for the main run's counters."""
        row = (
            await self._db.execute(
                select(
                    func.coalesce(func.sum(SessionTarget.tokens), 0),
                    func.coalesce(func.sum(SessionTarget.cost_usd), 0),
                ).where(SessionTarget.session_id == session_id)
            )
        ).first()
        if row is None:
            return 0, 0.0
        return int(row[0] or 0), float(row[1] or 0)


def _pr_objective(target: SessionTarget) -> str:
    """The objective recorded for a target's PR run (spec §15: one per target)."""
    title = target.title.strip()
    return f"Review PR #{target.number}: {title}" if title else f"Review PR #{target.number}"


async def finish_session_main_run(
    db: AsyncSession, session_id: uuid.UUID, status: AgentStatus, *, summary: str
) -> None:
    """Close the session's main node once the session itself is terminal (spec §15).

    The main run is the session's aggregation point: it ends when the session
    does, carrying the session's own totals. A session with no main run does not
    gain one here — starting the tree belongs to the submit path and to the target
    jobs, so a recompute only ever closes what exists.
    """
    recorder = RunRecorder(db)
    run_id = await recorder.store.finish_main_run(session_id, status)
    if run_id is None:
        return
    await recorder.emit(
        run_id,
        _terminal_event(status),
        {
            "status": status.value,
            "summary": summary,
            "step_count": 0,
        },
    )


class DatabaseEventSink(EventSink):
    """``agent_events`` rows: one per event, at the sequence the bus assigned it."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def emit(self, event: AgentEvent) -> None:
        """Persist one already-redacted event as it stands."""
        self._db.add(
            AgentEventRow(
                run_id=event.run_id,
                seq=event.seq,
                type=event.type.value,
                payload=event.payload,
                created_at=event.created_at,
            )
        )
        await self._db.flush()


class _ResumingEventBus(EventBus):
    """Event bus whose per-run counter can continue where a persisted log stopped."""

    def continue_run(self, run_id: uuid.UUID, last_seq: int) -> None:
        """Make the next event for ``run_id`` carry ``last_seq + 1``."""
        self._seq[run_id] = last_seq


class RunRecorder:
    """Writes one attempt's run events, continuing each run's persisted sequence."""

    def __init__(self, db: AsyncSession) -> None:
        self._store = DatabaseRunStore(db)
        self._bus = _ResumingEventBus(DatabaseEventSink(db))
        self._primed: set[uuid.UUID] = set()

    @property
    def store(self) -> DatabaseRunStore:
        """The run store writing through this recorder's session."""
        return self._store

    @property
    def bus(self) -> EventBus:
        """The bus the agent runtime fans its own runs' events out to."""
        return self._bus

    async def emit(
        self, run_id: uuid.UUID, type: EventType, payload: Mapping[str, object]
    ) -> None:
        """Emit one event for ``run_id``, after priming the bus from the log."""
        if run_id not in self._primed:
            self._primed.add(run_id)
            last_seq = await self._store.last_seq(run_id)
            if last_seq:
                self._bus.continue_run(run_id, last_seq)
        await self._bus.emit(run_id, type, payload)

    async def open_run(self, ref: RunRef, *, depth: int, model_id: str | None = None) -> None:
        """Announce a node the worker created: its spawn event, then its start.

        A run that was already there is left alone — a session's main run is
        announced once, by whoever created it, and a retried attempt reuses its
        PR run rather than spawning a second orchestrator (spec §15).
        """
        if not ref.created:
            return
        await self.emit(
            ref.id,
            EventType.SPAWNED,
            {
                "role": ref.role,
                "level": ref.level.value,
                "parent_run_id": _text(ref.parent_run_id),
                "target_id": _text(ref.target_id),
                "objective": ref.objective,
                "model_role": agent_spec(ref.role).model_role,
                "depth": depth,
            },
        )
        await self.start_run(ref, model_id=model_id)

    async def start_run(self, ref: RunRef, *, model_id: str | None = None) -> None:
        """Announce that an existing node starts again — a retried attempt (§15)."""
        await self.emit(
            ref.id,
            EventType.STARTED,
            {
                "role": ref.role,
                "model_id": model_id,
                "status": AgentStatus.RUNNING.value,
            },
        )

    async def finish(
        self,
        run_id: uuid.UUID,
        status: AgentStatus,
        *,
        summary: str,
        tokens: int = 0,
        cost_usd: float = 0.0,
        error: str | None = None,
        finding_count: int | None = None,
        step_count: int = 1,
    ) -> None:
        """Finish one run: stamp its row, then emit its single terminal event.

        The event is last on purpose, so a reader that sees the terminal event sees
        the row it describes (spec §8).
        """
        await self._store.finish_run(
            run_id=run_id, status=status, tokens=tokens, cost_usd=cost_usd, error=error
        )
        payload: dict[str, object] = {
            "status": status.value,
            "summary": summary,
            "tokens_used": tokens,
            "step_count": step_count,
        }
        if status is AgentStatus.FAILED:
            payload["error"] = error
        elif status is AgentStatus.DONE:
            payload["cost_usd"] = cost_usd
            payload["finding_count"] = finding_count or 0
        await self.emit(run_id, _terminal_event(status), payload)

    async def cancel_target_runs(self, target_id: uuid.UUID, *, summary: str) -> list[uuid.UUID]:
        """Cancel a target's still-open runs and log each one (spec §11)."""
        run_ids = await self._store.cancel_target_runs(target_id)
        for run_id in run_ids:
            await self.emit(
                run_id,
                EventType.CANCELLED,
                {
                    "status": AgentStatus.CANCELLED.value,
                    "summary": summary,
                    "step_count": 0,
                },
            )
        return run_ids


def _text(value: uuid.UUID | None) -> str | None:
    """Render an optional id for an event payload (``None`` stays ``None``)."""
    return None if value is None else str(value)


def _terminal_event(status: AgentStatus) -> EventType:
    """Map a terminal status onto its event type (spec §7)."""
    if status is AgentStatus.CANCELLED:
        return EventType.CANCELLED
    if status is AgentStatus.FAILED:
        return EventType.FAILED
    return EventType.COMPLETED
