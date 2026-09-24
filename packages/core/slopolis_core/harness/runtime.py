"""The Harness V1 agent loop: tool-loop, spawn tool, budgets, events (spec §5-§7, §11).

One :class:`AgentRuntime` runs a whole tree. :meth:`AgentRuntime.run` drives a
single agent; when that agent's spec allows spawning, its ``spawn_subagents``
tool calls ``run`` again for each child with ``parent_run_id`` set and
``depth + 1``. Everything the loop touches — the model gateway, the run store,
the event bus, the model resolver, and the read-only tools — is injected, so
``core`` keeps no DB, HTTP, or provider import.

The loop is deliberately the spec §5 shape: a bounded tool-loop with a
deterministic termination check, no ledgers, no re-planning. Budgets are checked
*before* each model call and each spawn; a stop returns a partial result with a
note instead of raising (spec §6, §11). A failing child comes back as a
``SubAgentResult`` with ``status=failed``; it never tears down its parent.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Final, Literal, Protocol, cast, runtime_checkable
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, ValidationError

from slopolis_core.findings import Finding, FindingsParseError, parse_findings
from slopolis_core.harness.context import RunRef, bind_run
from slopolis_core.harness.events import EventBus
from slopolis_core.harness.registry import (
    BUILT_IN_AGENTS,
    SPAWN_TOOL,
    UnknownRoleError,
    agent_spec,
)
from slopolis_core.harness.resolver import ModelResolutionError, ModelResolver
from slopolis_core.harness.types import (
    AgentSpec,
    AgentStatus,
    Aspect,
    EventType,
    HarnessLevel,
    Policy,
    SubAgentResult,
    SubtaskSpec,
)

__all__ = [
    "AgentResult",
    "AgentRuntime",
    "AssistantTurn",
    "LlmTurn",
    "Message",
    "RunStore",
    "Tool",
    "ToolCall",
    "ToolSpec",
]

#: Say this instead of "no final answer": a step-limit stop is a note, not an error.
_TOKEN_BUDGET_NOTE = "stopped before the next model call: run token budget exhausted"
_COST_BUDGET_NOTE = "stopped before the next model call: run cost budget exhausted"
_SESSION_COST_NOTE = "stopped before the next model call: session cost budget exhausted"
_SESSION_RUNS_NOTE = "stopped: session agent-run ceiling reached"

#: Sentinel a tool returns to ask the loop to stop for budget reasons.
_BUDGET_STOP: Final = object()

_SPAWN_DESCRIPTION = (
    "Delegate one or more scoped review tasks to sub-agents. The batch runs "
    "concurrently and returns each sub-agent's result in the order you submitted "
    "them. Sub-agents get only the objective and scope you pass here; they do not "
    "see this conversation."
)

#: JSON schema presented to the model for the spawn tool (spec §4, schema-validated).
_SPAWN_PARAMETERS: dict[str, object] = {
    "type": "object",
    "properties": {
        "tasks": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "properties": {
                    "role": {"type": "string", "enum": list(BUILT_IN_AGENTS)},
                    "objective": {"type": "string"},
                    "model_role": {"type": "string"},
                    "scope": {
                        "type": "object",
                        "properties": {
                            "target_id": {"type": "string"},
                            "paths": {"type": "array", "items": {"type": "string"}},
                            "aspect": {
                                "type": "string",
                                "enum": [aspect.value for aspect in Aspect],
                            },
                            "constraints": {"type": "array", "items": {"type": "string"}},
                        },
                    },
                },
                "required": ["role", "objective"],
            },
        }
    },
    "required": ["tasks"],
}


class Message(BaseModel):
    """One entry in a run's own transcript, in the provider-neutral wire shape."""

    model_config = ConfigDict(extra="forbid")

    role: Literal["system", "user", "assistant", "tool"]
    content: str
    tool_call_id: str | None = None


class ToolCall(BaseModel):
    """One tool invocation the model asked for in a turn."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    arguments: dict[str, object]


class AssistantTurn(BaseModel):
    """One model turn: text, the tools it wants called, and what it cost us."""

    model_config = ConfigDict(extra="forbid")

    text: str | None = None
    tool_calls: list[ToolCall] = []
    tokens: int = 0
    cost_usd: float = 0.0


class ToolSpec(BaseModel):
    """The description of a tool as presented to the model."""

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str
    parameters: dict[str, object]


@runtime_checkable
class LlmTurn(Protocol):
    """One chat completion with tool support; the worker adapts its gateway here."""

    async def complete(
        self, *, model_id: str, messages: list[Message], tools: list[ToolSpec]
    ) -> AssistantTurn: ...


@runtime_checkable
class Tool(Protocol):
    """A read-only tool the loop may dispatch to."""

    name: str
    spec: ToolSpec

    async def __call__(self, arguments: dict[str, object]) -> object: ...


@runtime_checkable
class RunStore(Protocol):
    """Persistence port for run rows; the worker adapts ``agent_run`` here."""

    async def create_run(
        self,
        *,
        session_id: UUID,
        target_id: UUID | None,
        parent_run_id: UUID | None,
        level: HarnessLevel,
        role: str,
        model_id: str,
        objective: str,
    ) -> UUID: ...

    async def finish_run(
        self,
        *,
        run_id: UUID,
        status: AgentStatus,
        tokens: int,
        cost_usd: float,
        error: str | None = None,
    ) -> None: ...


class AgentResult(BaseModel):
    """Typed outcome of one run, returned to its caller (a parent, or the worker)."""

    model_config = ConfigDict(extra="forbid")

    run_id: UUID
    status: AgentStatus
    summary: str
    findings: list[Finding] = []
    tokens: int = 0
    cost_usd: float = 0.0
    error: str | None = None
    children: list[SubAgentResult] = []


class _FunctionTool:
    """Adapts an async handler to the :class:`Tool` protocol."""

    def __init__(
        self,
        *,
        name: str,
        description: str,
        parameters: dict[str, object],
        handler: Callable[[dict[str, object]], Awaitable[object]],
    ) -> None:
        self.name = name
        self.spec = ToolSpec(name=name, description=description, parameters=parameters)
        self._handler = handler

    async def __call__(self, arguments: dict[str, object]) -> object:
        return await self._handler(arguments)


@dataclass(frozen=True)
class _RunContext:
    """Identity of one in-flight run, handed to the spawn tool and its children."""

    run_id: UUID
    session_id: UUID
    target_id: UUID | None
    depth: int
    model_id: str
    cancelled: Callable[[], bool]


@dataclass
class _RunState:
    """Mutable accumulators for one run's loop; never shared between runs."""

    history: list[Message] = field(default_factory=list)
    children: list[SubAgentResult] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    step_count: int = 0
    tokens: int = 0
    cost: float = 0.0
    spawned_children: int = 0
    final_text: str | None = None
    note: str | None = None
    error: str | None = None
    cancelled: bool = False


def _never_cancelled() -> bool:
    """Cancellation predicate for a run nobody can cancel."""
    return False


def _error_message(exc: BaseException) -> str:
    """A one-line, secret-free description of a failure."""
    return f"{type(exc).__name__}: {exc}"


def _json_default(value: object) -> object:
    """Serialize the non-JSON values a tool may return (UUID, enum, model)."""
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return str(value)


def _serialize(value: object) -> str:
    """Render a tool result as the text the model sees; never raises."""
    try:
        return json.dumps(value, default=_json_default, ensure_ascii=False)
    except (TypeError, ValueError):
        return json.dumps(
            {"error": "unserializable_result", "detail": type(value).__name__}
        )


def _step_summary(turn: AssistantTurn, step: int) -> str:
    """A compact, non-transcript description of one model turn."""
    if turn.tool_calls:
        return f"step {step}: {len(turn.tool_calls)} tool call(s)"
    return f"step {step}: final output"


def _as_error_code(result: object) -> str | None:
    """The typed error code of a failed tool result, or None when it succeeded."""
    if not isinstance(result, dict):
        return None
    error = cast("dict[str, object]", result).get("error")
    return None if error is None else str(error)


def _result_summary(result: object) -> str:
    """A compact description of a tool result for the ``agent.tool_result`` event."""
    code = _as_error_code(result)
    if code is not None:
        return code
    if isinstance(result, list):
        return f"{len(cast('list[object]', result))} item(s)"
    return result.__class__.__name__


def _tool_ok(result: object) -> bool:
    """Whether a tool result counts as a success (a typed error dict does not)."""
    return _as_error_code(result) is None


def _result_count(result: object) -> int:
    """How many items a tool returned, for the event payload."""
    return len(cast("list[object]", result)) if isinstance(result, list) else 1


def _parse_final(text: str | None) -> list[Finding]:
    """Parse the final text as findings JSON; unparsable text yields no findings."""
    if not text or not text.strip():
        return []
    try:
        return parse_findings(text)
    except FindingsParseError:
        return []


def _compose_summary(note: str | None, text: str | None) -> str:
    """Join a stop note with the model's own text, note first."""
    parts = [part for part in (note, text) if part]
    return "\n\n".join(parts)


def _finding_payload(finding: Finding) -> dict[str, object]:
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


def _parse_tasks(arguments: Mapping[str, object]) -> list[SubtaskSpec] | None:
    """Validate the spawn tool's ``tasks`` argument, or return None (spec §10)."""
    raw = arguments.get("tasks")
    if not isinstance(raw, list):
        return None
    items = cast("list[object]", raw)
    if not items:
        return None
    tasks: list[SubtaskSpec] = []
    for item in items:
        try:
            tasks.append(SubtaskSpec.model_validate(item))
        except ValidationError:
            return None
    return tasks


def _resolve_tasks(
    tasks: list[SubtaskSpec],
) -> list[tuple[SubtaskSpec, AgentSpec]] | None:
    """Bind each sub-task to its registry spec, or return None on an unknown role."""
    resolved: list[tuple[SubtaskSpec, AgentSpec]] = []
    for subtask in tasks:
        try:
            resolved.append((subtask, agent_spec(subtask.role)))
        except UnknownRoleError:
            return None
    return resolved


def _scoped_task(subtask: SubtaskSpec) -> str:
    """Render a sub-agent's task: its objective plus scope, and nothing else (spec §4)."""
    lines = ["OBJECTIVE", subtask.objective]
    if subtask.scope.aspect is not None:
        lines += ["", f"ASPECT: {subtask.scope.aspect.value}"]
    if subtask.scope.paths:
        lines += ["", "PATHS:", *(f"- {path}" for path in subtask.scope.paths)]
    if subtask.scope.constraints:
        lines += ["", "CONSTRAINTS:", *(f"- {rule}" for rule in subtask.scope.constraints)]
    return "\n".join(lines)


class AgentRuntime:
    """Runs agents over the spec §5 tool-loop, with budgets, events, and spawning."""

    def __init__(
        self,
        *,
        llm: LlmTurn,
        store: RunStore,
        bus: EventBus,
        policy: Policy,
        resolver: ModelResolver,
        tools: Mapping[str, Tool] | None = None,
    ) -> None:
        self._llm = llm
        self._store = store
        self._bus = bus
        self._policy = policy
        self._resolver = resolver
        self._tools: dict[str, Tool] = dict(tools) if tools is not None else {}
        self._lock = asyncio.Lock()
        self._runs_started = 0
        self._reserved_runs = 0
        self._session_cost = 0.0

    async def run(
        self,
        spec: AgentSpec,
        *,
        task: str,
        session_id: UUID,
        target_id: UUID | None = None,
        parent_run_id: UUID | None = None,
        depth: int = 0,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> AgentResult:
        """Run one agent to completion and return its typed result.

        Never raises for a model, tool, or child failure: the failure is recorded
        on the run (``agent.failed``) and in the returned result, so one bad node
        degrades its own subtree and nothing else (spec §11).
        """
        if not await self._claim_run_slot():
            return AgentResult(
                run_id=uuid4(), status=AgentStatus.DONE, summary=_SESSION_RUNS_NOTE
            )

        try:
            choice = await self._resolver.resolve(spec.model_role)
        except ModelResolutionError as exc:
            # No model means no model id to record, so there is no run row to own
            # the failure; the caller still gets a typed result instead of a raise.
            return AgentResult(
                run_id=uuid4(),
                status=AgentStatus.FAILED,
                summary="",
                error=_error_message(exc),
            )

        run_id = await self._store.create_run(
            session_id=session_id,
            target_id=target_id,
            parent_run_id=parent_run_id,
            level=spec.level,
            role=spec.name,
            model_id=choice.model_id,
            objective=task,
        )
        ctx = _RunContext(
            run_id=run_id,
            session_id=session_id,
            target_id=target_id,
            depth=depth,
            model_id=choice.model_id,
            cancelled=_never_cancelled if is_cancelled is None else is_cancelled,
        )
        await self._bus.emit(
            run_id,
            EventType.SPAWNED,
            {
                "role": spec.name,
                "level": spec.level.value,
                "parent_run_id": None if parent_run_id is None else str(parent_run_id),
                "target_id": None if target_id is None else str(target_id),
                "objective": task,
                "model_role": spec.model_role,
                "depth": depth,
            },
        )
        await self._bus.emit(
            run_id,
            EventType.STARTED,
            {"role": spec.name, "model_id": choice.model_id, "status": AgentStatus.RUNNING.value},
        )

        state = _RunState()
        # The run is published while it executes so a model call that originates
        # below this loop — the review harness composing its own prompt — can say
        # which run it belongs to (spec v2 11.3).
        ref = RunRef(
            run_id=run_id,
            session_id=session_id,
            target_id=target_id,
            level=spec.level,
            role=spec.name,
            model_id=choice.model_id,
        )
        with bind_run(ref):
            await self._drive(spec, state, ctx, task)
            return await self._finish(state, ctx)

    async def _drive(
        self, spec: AgentSpec, state: _RunState, ctx: _RunContext, task: str
    ) -> None:
        """Run the bounded tool-loop, recording the outcome on ``state``."""
        spawn_offered = spec.can_spawn and ctx.depth < self._policy.max_depth
        spawn_tool = _FunctionTool(
            name=SPAWN_TOOL,
            description=_SPAWN_DESCRIPTION,
            parameters=_SPAWN_PARAMETERS,
            handler=self._spawn_handler(spec, state, ctx),
        )
        offered: list[ToolSpec] = []
        for name in spec.tools:
            if name == SPAWN_TOOL:
                if spawn_offered:
                    offered.append(spawn_tool.spec)
            elif name in self._tools:
                offered.append(self._tools[name].spec)

        state.history = [
            Message(role="system", content=spec.system_prompt),
            Message(role="user", content=task),
        ]
        max_steps = self._max_steps(spec)

        for _ in range(max_steps):
            if ctx.cancelled():
                state.cancelled = True
                return
            note = self._budget_note(state.tokens, state.cost)
            if note is not None:
                state.note = note
                return
            try:
                turn = await self._llm.complete(
                    model_id=ctx.model_id, messages=list(state.history), tools=list(offered)
                )
            except Exception as exc:
                state.error = _error_message(exc)
                return

            state.step_count += 1
            state.tokens += turn.tokens
            state.cost += turn.cost_usd
            await self._add_session_cost(turn.cost_usd)
            await self._bus.emit(
                ctx.run_id,
                EventType.STEP,
                {
                    "step": state.step_count,
                    "model_id": ctx.model_id,
                    "summary": _step_summary(turn, state.step_count),
                    "tool_call_count": len(turn.tool_calls),
                },
            )
            if turn.text:
                await self._bus.emit(
                    ctx.run_id,
                    EventType.MESSAGE,
                    {"role": "assistant", "summary": turn.text, "chars": len(turn.text)},
                )
            if not turn.tool_calls:
                state.final_text = turn.text
                return

            state.history.append(Message(role="assistant", content=turn.text or ""))
            for call in turn.tool_calls:
                await self._bus.emit(
                    ctx.run_id,
                    EventType.TOOL_CALL,
                    {
                        "tool": call.name,
                        "args_summary": _serialize(call.arguments),
                        "step": state.step_count,
                        "call_id": call.id,
                    },
                )
                result = await self._dispatch(call, spec, spawn_tool if spawn_offered else None)
                if state.note is not None:
                    await self._bus.emit(
                        ctx.run_id,
                        EventType.TOOL_RESULT,
                        {
                            "tool": call.name,
                            "ok": False,
                            "summary": state.note,
                            "step": state.step_count,
                            "call_id": call.id,
                            "result_count": 0,
                        },
                    )
                    return
                await self._bus.emit(
                    ctx.run_id,
                    EventType.TOOL_RESULT,
                    {
                        "tool": call.name,
                        "ok": _tool_ok(result),
                        "summary": _result_summary(result),
                        "step": state.step_count,
                        "call_id": call.id,
                        "result_count": _result_count(result),
                    },
                )
                state.history.append(
                    Message(role="tool", content=_serialize(result), tool_call_id=call.id)
                )

        state.note = f"stopped after {max_steps} steps: step limit reached"

    async def _dispatch(
        self, call: ToolCall, spec: AgentSpec, spawn_tool: _FunctionTool | None
    ) -> object:
        """Execute one tool call, turning every failure into a typed tool error."""
        if call.name not in spec.tools:
            return {"error": "unknown_tool", "detail": call.name}
        tool: Tool
        if call.name == SPAWN_TOOL:
            if spawn_tool is None:
                # Only reachable when the model calls a tool it was not offered:
                # at ``max_depth`` the spawn tool is withheld entirely (spec §2).
                return {"error": "max_depth_exceeded"}
            tool = spawn_tool
        else:
            found = self._tools.get(call.name)
            if found is None:
                return {"error": "unknown_tool", "detail": call.name}
            tool = found
        try:
            return await tool(call.arguments)
        except Exception as exc:
            return {"error": "tool_failed", "detail": _error_message(exc)}

    def _spawn_handler(
        self, spec: AgentSpec, state: _RunState, ctx: _RunContext
    ) -> Callable[[dict[str, object]], Awaitable[object]]:
        """Build the spawn tool handler bound to one run's budget and counters."""

        async def _spawn(arguments: dict[str, object]) -> object:
            if not spec.can_spawn or ctx.depth + 1 > self._policy.max_depth:
                return {"error": "max_depth_exceeded"}
            tasks = _parse_tasks(arguments)
            if tasks is None:
                return {"error": "invalid_arguments"}
            if state.spawned_children + len(tasks) > self._policy.max_children_per_orchestrator:
                return {"error": "max_children_exceeded"}
            resolved = _resolve_tasks(tasks)
            if resolved is None:
                return {"error": "unknown_role"}

            note = self._budget_note(state.tokens, state.cost)
            if note is not None:
                state.note = note
                return _BUDGET_STOP
            if not await self._reserve_runs(len(resolved)):
                state.note = _SESSION_RUNS_NOTE
                return _BUDGET_STOP

            state.spawned_children += len(resolved)
            results = await self._run_batch(resolved, ctx)
            state.children.extend(results)
            return [result.model_dump(mode="json") for result in results]

        return _spawn

    async def _run_batch(
        self, resolved: list[tuple[SubtaskSpec, AgentSpec]], ctx: _RunContext
    ) -> list[SubAgentResult]:
        """Run one spawn batch concurrently, returning results in submission order."""
        # A zero (tightened) concurrency bound would deadlock, so it means "one".
        semaphore = asyncio.Semaphore(max(1, self._policy.max_concurrent_children))

        async def _one(subtask: SubtaskSpec, spec: AgentSpec) -> SubAgentResult:
            async with semaphore:
                return await self._run_child(subtask, spec, ctx)

        return list(
            await asyncio.gather(*(_one(subtask, spec) for subtask, spec in resolved))
        )

    async def _run_child(
        self, subtask: SubtaskSpec, spec: AgentSpec, ctx: _RunContext
    ) -> SubAgentResult:
        """Run one child, converting any failure into a failed ``SubAgentResult``."""
        try:
            result = await self.run(
                spec,
                task=_scoped_task(subtask),
                session_id=ctx.session_id,
                target_id=(
                    subtask.scope.target_id
                    if subtask.scope.target_id is not None
                    else ctx.target_id
                ),
                parent_run_id=ctx.run_id,
                depth=ctx.depth + 1,
                is_cancelled=ctx.cancelled,
            )
        except Exception as exc:
            return SubAgentResult(
                run_id=uuid4(),
                role=spec.name,
                status=AgentStatus.FAILED,
                summary="",
                error=_error_message(exc),
            )
        return SubAgentResult(
            run_id=result.run_id,
            role=spec.name,
            status=result.status,
            summary=result.summary,
            findings=result.findings,
            tokens_used=result.tokens,
            cost_usd=result.cost_usd,
            error=result.error,
        )

    async def _finish(self, state: _RunState, ctx: _RunContext) -> AgentResult:
        """Persist and announce the run's terminal state, then return its result."""
        if state.final_text is not None:
            state.findings = _parse_final(state.final_text)
        summary = _compose_summary(state.note, state.final_text)
        if state.cancelled:
            status = AgentStatus.CANCELLED
        elif state.error is not None:
            status = AgentStatus.FAILED
        else:
            status = AgentStatus.DONE

        await self._store.finish_run(
            run_id=ctx.run_id,
            status=status,
            tokens=state.tokens,
            cost_usd=state.cost,
            error=state.error,
        )
        for finding in state.findings:
            await self._bus.emit(ctx.run_id, EventType.FINDING, _finding_payload(finding))
        await self._bus.emit(
            ctx.run_id, _terminal_event(status), _terminal_payload(status, state, summary)
        )
        return AgentResult(
            run_id=ctx.run_id,
            status=status,
            summary=summary,
            findings=state.findings,
            tokens=state.tokens,
            cost_usd=state.cost,
            error=state.error,
            children=state.children,
        )

    def _max_steps(self, spec: AgentSpec) -> int:
        """The spec's step cap, tightened further when policy lowers it (spec §6, §9)."""
        cap = self._policy.max_steps_per_role.get(spec.name)
        return spec.max_steps if cap is None else min(spec.max_steps, cap)

    def _budget_note(self, tokens: int, cost: float) -> str | None:
        """Return why the next model call must not happen, or None when there is room."""
        if tokens >= self._policy.max_tokens_per_run:
            return _TOKEN_BUDGET_NOTE
        if cost >= self._policy.max_cost_usd_per_run:
            return _COST_BUDGET_NOTE
        if self._session_cost >= self._policy.max_session_cost_usd:
            return _SESSION_COST_NOTE
        return None

    async def _claim_run_slot(self) -> bool:
        """Claim one slot against ``max_total_agent_runs``, using reservations first."""
        async with self._lock:
            if self._reserved_runs > 0:
                self._reserved_runs -= 1
                self._runs_started += 1
                return True
            if self._runs_started >= self._policy.max_total_agent_runs:
                return False
            self._runs_started += 1
            return True

    async def _reserve_runs(self, count: int) -> bool:
        """Atomically reserve ``count`` run slots for a spawn batch (spec §6)."""
        async with self._lock:
            if (
                self._runs_started + self._reserved_runs + count
                > self._policy.max_total_agent_runs
            ):
                return False
            self._reserved_runs += count
            return True

    async def _add_session_cost(self, cost: float) -> None:
        """Add one turn's cost to the session-wide total."""
        async with self._lock:
            self._session_cost += cost


def _terminal_event(status: AgentStatus) -> EventType:
    """Map a terminal status onto its event type (spec §7)."""
    if status is AgentStatus.CANCELLED:
        return EventType.CANCELLED
    if status is AgentStatus.FAILED:
        return EventType.FAILED
    return EventType.COMPLETED


def _terminal_payload(
    status: AgentStatus, state: _RunState, summary: str
) -> dict[str, object]:
    """Build the allow-listed payload for the single terminal event."""
    if status is AgentStatus.CANCELLED:
        return {"status": status.value, "summary": summary, "step_count": state.step_count}
    if status is AgentStatus.FAILED:
        return {
            "status": status.value,
            "error": state.error,
            "summary": summary,
            "tokens_used": state.tokens,
            "step_count": state.step_count,
        }
    return {
        "status": status.value,
        "summary": summary,
        "tokens_used": state.tokens,
        "cost_usd": state.cost,
        "finding_count": len(state.findings),
        "step_count": state.step_count,
    }
