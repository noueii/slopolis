"""Tests for the Harness V1 agent runtime (spec v2 §4-§7, §11).

Every test drives the real loop with injected doubles: a scripted model, a
recording run store, and a collecting event sink. Nothing here reaches a
database, a gateway, or the network.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import cast
from uuid import UUID, uuid4

import pytest

from slopolis_core.harness import (
    READ_ONLY_TOOLS,
    SPAWN_TOOL,
    AgentRuntime,
    AgentSpec,
    AgentStatus,
    AssistantTurn,
    CollectingSink,
    EventBus,
    EventType,
    HarnessLevel,
    LlmTurn,
    Message,
    ModelChoice,
    ModelResolver,
    Policy,
    RunRef,
    RunStore,
    StaticResolver,
    Tool,
    ToolCall,
    ToolSpec,
    agent_spec,
    current_run,
    model_roles,
)

#: A valid findings envelope with one finding, as the model must emit it.
_FINDINGS_JSON = json.dumps(
    {
        "findings": [
            {
                "path": "src/a.py",
                "line": 3,
                "severity": "error",
                "category": "correctness",
                "message": "boom",
                "suggestion": None,
                "confidence": 0.8,
            }
        ]
    }
)

_PLAIN_FINDINGS = '{"findings": []}'


@dataclass
class _Call:
    """One recorded ``LlmTurn.complete`` invocation."""

    key: str
    model_id: str
    messages: list[Message]
    tools: list[ToolSpec]


class ScriptedLlm:
    """Replays a script per task key and records calls, overlap, and hooks."""

    def __init__(
        self,
        script: Mapping[str, list[AssistantTurn]],
        *,
        delays: Mapping[str, float] | None = None,
        failures: Mapping[str, str] | None = None,
    ) -> None:
        self._script = {key: list(turns) for key, turns in script.items()}
        self._delays = dict(delays or {})
        self._failures = dict(failures or {})
        self.calls: list[_Call] = []
        self.hooks: dict[str, Callable[[], None]] = {}
        self.in_flight = 0
        self.max_in_flight = 0

    def _key(self, messages: list[Message]) -> str:
        """Match the first user message against the longest script key it contains."""
        for message in messages:
            if message.role != "user":
                continue
            for key in sorted(self._script, key=len, reverse=True):
                if key in message.content:
                    return key
            break
        raise AssertionError(f"no scripted turns for {[m.content for m in messages]!r}")

    async def complete(
        self, *, model_id: str, messages: list[Message], tools: list[ToolSpec]
    ) -> AssistantTurn:
        key = self._key(messages)
        self.calls.append(_Call(key=key, model_id=model_id, messages=messages, tools=tools))
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await asyncio.sleep(self._delays.get(key, 0.01))
            hook = self.hooks.get(key)
            if hook is not None:
                hook()
            failure = self._failures.get(key)
            if failure is not None:
                raise RuntimeError(failure)
            turns = self._script[key]
            if not turns:
                raise AssertionError(f"script for {key!r} is exhausted")
            return turns.pop(0)
        finally:
            self.in_flight -= 1

    def calls_for(self, key: str) -> list[_Call]:
        """Every recorded call that served the script key ``key``."""
        return [call for call in self.calls if call.key == key]

    def tool_messages(self, key: str) -> list[object]:
        """The tool results handed back to the model on its most recent call."""
        calls = self.calls_for(key)
        assert calls, f"no call for {key!r}"
        return [
            json.loads(message.content)
            for message in calls[-1].messages
            if message.role == "tool"
        ]


@dataclass
class _CreatedRun:
    run_id: UUID
    session_id: UUID
    target_id: UUID | None
    parent_run_id: UUID | None
    level: HarnessLevel
    role: str
    model_id: str
    objective: str


@dataclass
class _FinishedRun:
    run_id: UUID
    status: AgentStatus
    tokens: int
    cost_usd: float
    error: str | None


class RecordingStore:
    """In-memory ``RunStore`` that records every row it is asked to write."""

    def __init__(self) -> None:
        self.created: list[_CreatedRun] = []
        self.finished: list[_FinishedRun] = []

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
    ) -> UUID:
        run_id = uuid4()
        self.created.append(
            _CreatedRun(
                run_id=run_id,
                session_id=session_id,
                target_id=target_id,
                parent_run_id=parent_run_id,
                level=level,
                role=role,
                model_id=model_id,
                objective=objective,
            )
        )
        return run_id

    async def finish_run(
        self,
        *,
        run_id: UUID,
        status: AgentStatus,
        tokens: int,
        cost_usd: float,
        error: str | None = None,
    ) -> None:
        self.finished.append(
            _FinishedRun(
                run_id=run_id, status=status, tokens=tokens, cost_usd=cost_usd, error=error
            )
        )

    def row(self, role: str) -> _CreatedRun:
        """The single created row for ``role``."""
        rows = [row for row in self.created if row.role == role]
        assert len(rows) == 1, f"expected one {role!r} row, got {len(rows)}"
        return rows[0]

    def children_of(self, run_id: UUID) -> list[_CreatedRun]:
        """Every row whose parent is ``run_id``."""
        return [row for row in self.created if row.parent_run_id == run_id]

    def finish_of(self, run_id: UUID) -> _FinishedRun:
        """The terminal write for ``run_id``."""
        rows = [row for row in self.finished if row.run_id == run_id]
        assert len(rows) == 1, f"expected one terminal write, got {len(rows)}"
        return rows[0]


class FakeTool:
    """Read-only tool double: records arguments and returns a canned value."""

    def __init__(self, name: str, result: object = "ok") -> None:
        self.name = name
        self.spec = ToolSpec(
            name=name, description=f"fake {name}", parameters={"type": "object"}
        )
        self.result = result
        self.calls: list[dict[str, object]] = []

    async def __call__(self, arguments: dict[str, object]) -> object:
        self.calls.append(arguments)
        return self.result


class Flag:
    """Cancellation predicate a test can flip mid-run."""

    def __init__(self) -> None:
        self.value = False

    def __call__(self) -> bool:
        return self.value


def _set_true(flag: Flag) -> Callable[[], None]:
    """A hook that trips ``flag`` while the model serves its turn."""

    def _hook() -> None:
        flag.value = True

    return _hook


@dataclass
class Harness:
    """The doubles a test drives, bundled."""

    runtime: AgentRuntime
    store: RecordingStore
    sink: CollectingSink
    llm: ScriptedLlm


def _resolver() -> StaticResolver:
    """Bind every built-in model role to a distinct, recognizable model id."""
    return StaticResolver(
        {
            role: ModelChoice(model_id=f"model-{role}", provider="litellm")
            for role in model_roles()
        }
    )


def _read_tools() -> dict[str, Tool]:
    """One fake per read-only tool name (spec §10)."""
    return {name: FakeTool(name) for name in READ_ONLY_TOOLS}


def _build(
    llm: ScriptedLlm,
    *,
    policy: Policy | None = None,
    tools: Mapping[str, Tool] | None = None,
) -> Harness:
    store = RecordingStore()
    sink = CollectingSink()
    runtime = AgentRuntime(
        llm=llm,
        store=store,
        bus=EventBus(sink),
        policy=policy or Policy(),
        resolver=_resolver(),
        tools=tools,
    )
    return Harness(runtime=runtime, store=store, sink=sink, llm=llm)


def _types(sink: CollectingSink, run_id: UUID) -> list[EventType]:
    """The event types emitted for one run, in order."""
    return [event.type for event in sink.events if event.run_id == run_id]


def _payloads(
    sink: CollectingSink, run_id: UUID, event_type: EventType
) -> list[dict[str, object]]:
    """The payloads of one event type for one run."""
    return [
        event.payload
        for event in sink.events
        if event.run_id == run_id and event.type is event_type
    ]


def _spawn_turn(
    tasks: list[dict[str, object]], *, call_id: str = "call-spawn", text: str = "delegating"
) -> AssistantTurn:
    """An assistant turn that calls the spawn tool with ``tasks``."""
    return AssistantTurn(
        text=text,
        tool_calls=[ToolCall(id=call_id, name=SPAWN_TOOL, arguments={"tasks": tasks})],
        tokens=100,
        cost_usd=0.01,
    )


def _read_turn(call_id: str, path: str) -> AssistantTurn:
    """An assistant turn that calls one read-only tool."""
    return AssistantTurn(
        text="reading",
        tool_calls=[ToolCall(id=call_id, name="read_diff", arguments={"path": path})],
        tokens=20,
        cost_usd=0.01,
    )


def test_the_doubles_satisfy_the_runtime_protocols() -> None:
    assert isinstance(ScriptedLlm({}), LlmTurn)
    assert isinstance(RecordingStore(), RunStore)
    assert isinstance(FakeTool("read_file"), Tool)
    assert isinstance(_resolver(), ModelResolver)


async def test_single_turn_agent_records_its_run_and_emits_the_sequence() -> None:
    # Given an agent whose model answers once, with findings
    llm = ScriptedLlm(
        {"review the diff": [AssistantTurn(text=_FINDINGS_JSON, tokens=120, cost_usd=0.02)]}
    )
    harness = _build(llm, tools=_read_tools())
    session_id, target_id, parent_run_id = uuid4(), uuid4(), uuid4()

    # When it runs
    result = await harness.runtime.run(
        agent_spec("logic-reviewer"),
        task="review the diff",
        session_id=session_id,
        target_id=target_id,
        parent_run_id=parent_run_id,
        depth=1,
    )

    # Then the run row, the result, and the event sequence all agree
    row = harness.store.row("logic-reviewer")
    assert row.session_id == session_id
    assert row.target_id == target_id
    assert row.parent_run_id == parent_run_id
    assert row.level is HarnessLevel.SUB
    assert row.model_id == "model-review.specialist"
    assert row.objective == "review the diff"

    assert result.run_id == row.run_id
    assert result.status is AgentStatus.DONE
    assert result.tokens == 120
    assert result.cost_usd == 0.02
    assert result.error is None
    assert [finding.path for finding in result.findings] == ["src/a.py"]

    assert _types(harness.sink, row.run_id) == [
        EventType.SPAWNED,
        EventType.STARTED,
        EventType.STEP,
        EventType.MESSAGE,
        EventType.FINDING,
        EventType.COMPLETED,
    ]

    finished = harness.store.finish_of(row.run_id)
    assert finished.status is AgentStatus.DONE
    assert finished.tokens == 120
    assert finished.error is None

    # And the model saw the role's system prompt, the task, and its allow-list
    call = llm.calls_for("review the diff")[0]
    assert call.model_id == "model-review.specialist"
    assert [message.role for message in call.messages] == ["system", "user"]
    assert call.messages[0].content == agent_spec("logic-reviewer").system_prompt
    assert [tool_spec.name for tool_spec in call.tools] == list(READ_ONLY_TOOLS)


async def test_unparsable_final_output_yields_a_summary_not_an_error() -> None:
    # Given a model that answers in prose instead of findings JSON
    llm = ScriptedLlm(
        {"review the diff": [AssistantTurn(text="I could not analyse this diff.")]}
    )
    harness = _build(llm)

    # When it runs
    result = await harness.runtime.run(
        agent_spec("context-gatherer"), task="review the diff", session_id=uuid4()
    )

    # Then the run still completes, with the prose as its summary
    assert result.status is AgentStatus.DONE
    assert result.findings == []
    assert result.summary == "I could not analyse this diff."
    assert result.error is None


async def test_parent_spawns_two_children_in_submission_order() -> None:
    # Given an orchestrator that delegates two aspects, the first being slower
    llm = ScriptedLlm(
        {
            "review two pull requests": [
                _spawn_turn(
                    [
                        {"role": "logic-reviewer", "objective": "check the logic"},
                        {"role": "security-reviewer", "objective": "check the secrets"},
                    ]
                ),
                AssistantTurn(text=_PLAIN_FINDINGS, tokens=10, cost_usd=0.002),
            ],
            "check the logic": [AssistantTurn(text=_FINDINGS_JSON, tokens=20, cost_usd=0.003)],
            "check the secrets": [AssistantTurn(text=_PLAIN_FINDINGS, tokens=30, cost_usd=0.004)],
        },
        delays={"check the logic": 0.05},
    )
    harness = _build(llm)
    session_id, target_id = uuid4(), uuid4()

    # When the orchestrator runs
    result = await harness.runtime.run(
        agent_spec("orchestrator.pr"),
        task="review two pull requests",
        session_id=session_id,
        target_id=target_id,
    )

    # Then all three rows exist, wired parent to child
    parent = harness.store.row("orchestrator.pr")
    children = harness.store.children_of(parent.run_id)
    assert [child.role for child in children] == ["logic-reviewer", "security-reviewer"]
    assert [child.level for child in children] == [HarnessLevel.SUB, HarnessLevel.SUB]
    assert [child.model_id for child in children] == [
        "model-review.specialist",
        "model-review.security",
    ]
    # A sub-task without a target of its own is scoped to the parent's PR
    assert [child.target_id for child in children] == [target_id, target_id]
    assert [child.session_id for child in children] == [session_id, session_id]

    # And results come back in submission order even though the first was slower
    assert result.status is AgentStatus.DONE
    assert [child.role for child in result.children] == [
        "logic-reviewer",
        "security-reviewer",
    ]
    assert [child.status for child in result.children] == [
        AgentStatus.DONE,
        AgentStatus.DONE,
    ]
    assert [child.tokens_used for child in result.children] == [20, 30]
    assert result.children[0].findings[0].path == "src/a.py"
    assert result.tokens == 110

    # The orchestrator's own timeline: one delegated turn, then its answer
    assert _types(harness.sink, parent.run_id) == [
        EventType.SPAWNED,
        EventType.STARTED,
        EventType.STEP,
        EventType.MESSAGE,
        EventType.TOOL_CALL,
        EventType.TOOL_RESULT,
        EventType.STEP,
        EventType.MESSAGE,
        EventType.COMPLETED,
    ]
    assert _types(harness.sink, children[0].run_id) == [
        EventType.SPAWNED,
        EventType.STARTED,
        EventType.STEP,
        EventType.MESSAGE,
        EventType.FINDING,
        EventType.COMPLETED,
    ]
    assert _types(harness.sink, children[1].run_id) == [
        EventType.SPAWNED,
        EventType.STARTED,
        EventType.STEP,
        EventType.MESSAGE,
        EventType.COMPLETED,
    ]

    spawned = _payloads(harness.sink, children[0].run_id, EventType.SPAWNED)[0]
    assert spawned["parent_run_id"] == str(parent.run_id)
    assert spawned["target_id"] == str(target_id)
    assert spawned["level"] == "sub"
    assert spawned["depth"] == 1
    assert spawned["model_role"] == "review.specialist"

    tool_result = _payloads(harness.sink, parent.run_id, EventType.TOOL_RESULT)[0]
    assert tool_result["ok"] is True
    assert tool_result["result_count"] == 2

    # The model got both children back, in order, as JSON
    handed_back = llm.tool_messages("review two pull requests")[0]
    rows = cast("list[dict[str, object]]", handed_back)
    assert [item["role"] for item in rows] == [
        "logic-reviewer",
        "security-reviewer",
    ]


async def test_spawn_batch_never_exceeds_the_concurrency_bound() -> None:
    # Given four delegations and a bound of two
    tasks: list[dict[str, object]] = [
        {"role": "context-gatherer", "objective": f"collect context {index}"}
        for index in range(4)
    ]
    script = {
        "review the session": [
            _spawn_turn(tasks),
            AssistantTurn(text=_PLAIN_FINDINGS),
        ],
        **{
            f"collect context {index}": [AssistantTurn(text=_PLAIN_FINDINGS)]
            for index in range(4)
        },
    }
    harness = _build(ScriptedLlm(script), policy=Policy(max_concurrent_children=2))

    # When the orchestrator runs
    result = await harness.runtime.run(
        agent_spec("orchestrator.pr"), task="review the session", session_id=uuid4()
    )

    # Then at most two children were ever in flight, and all four came back
    assert harness.llm.max_in_flight == 2
    assert [child.status for child in result.children] == [AgentStatus.DONE] * 4


async def test_unknown_role_is_rejected_without_raising() -> None:
    # Given a delegation naming a role that is not built in
    llm = ScriptedLlm(
        {
            "review the change": [
                _spawn_turn([{"role": "ghost-reviewer", "objective": "haunt the diff"}]),
                AssistantTurn(text=_PLAIN_FINDINGS),
            ]
        }
    )
    harness = _build(llm)

    # When the orchestrator runs
    result = await harness.runtime.run(
        agent_spec("orchestrator.pr"), task="review the change", session_id=uuid4()
    )

    # Then the rejection is a typed tool error the model can recover from
    assert result.status is AgentStatus.DONE
    assert result.children == []
    assert len(harness.store.created) == 1
    assert llm.tool_messages("review the change") == [{"error": "unknown_role"}]
    assert _payloads(harness.sink, result.run_id, EventType.TOOL_RESULT)[0]["ok"] is False


async def test_spawn_tool_is_withheld_at_max_depth_and_rejected_if_called() -> None:
    # Given an orchestrator already at the policy depth limit
    llm = ScriptedLlm(
        {
            "review the change": [
                _spawn_turn([{"role": "logic-reviewer", "objective": "check the logic"}]),
                AssistantTurn(text=_PLAIN_FINDINGS),
            ]
        }
    )
    harness = _build(llm, tools=_read_tools())

    # When it runs, even though its model tries to delegate anyway
    result = await harness.runtime.run(
        agent_spec("orchestrator.pr"),
        task="review the change",
        session_id=uuid4(),
        depth=Policy().max_depth,
    )

    # Then the tool was never offered and the call is a typed rejection
    offered = llm.calls_for("review the change")[0].tools
    assert SPAWN_TOOL not in [tool_spec.name for tool_spec in offered]
    assert llm.tool_messages("review the change") == [{"error": "max_depth_exceeded"}]
    assert result.children == []
    assert len(harness.store.created) == 1


async def test_sub_level_agent_never_gets_the_spawn_tool() -> None:
    # Given a review sub-agent
    llm = ScriptedLlm({"gather the context": [AssistantTurn(text=_PLAIN_FINDINGS)]})
    harness = _build(llm, tools=_read_tools())

    # When it runs
    await harness.runtime.run(
        agent_spec("context-gatherer"), task="gather the context", session_id=uuid4()
    )

    # Then only read-only tools were offered
    offered = llm.calls_for("gather the context")[0].tools
    assert [tool_spec.name for tool_spec in offered] == list(READ_ONLY_TOOLS)


async def test_children_per_orchestrator_cap_is_enforced_across_a_run() -> None:
    # Given a bound of two children for the whole orchestrator
    def task(objective: str) -> dict[str, object]:
        return {"role": "context-gatherer", "objective": objective}

    llm = ScriptedLlm(
        {
            "review the session": [
                _spawn_turn([task("collect context a"), task("collect context b")]),
                _spawn_turn([task("collect context c")], call_id="call-spawn-2"),
                AssistantTurn(text=_PLAIN_FINDINGS),
            ],
            "collect context a": [AssistantTurn(text=_PLAIN_FINDINGS)],
            "collect context b": [AssistantTurn(text=_PLAIN_FINDINGS)],
            "collect context c": [AssistantTurn(text=_PLAIN_FINDINGS)],
        }
    )
    harness = _build(llm, policy=Policy(max_children_per_orchestrator=2))

    # When the orchestrator delegates two, then one more
    result = await harness.runtime.run(
        agent_spec("orchestrator.pr"), task="review the session", session_id=uuid4()
    )

    # Then the overflow batch is rejected and no third child run exists
    assert result.status is AgentStatus.DONE
    assert [child.role for child in result.children] == ["context-gatherer"] * 2
    assert len(harness.store.children_of(result.run_id)) == 2
    assert llm.tool_messages("review the session")[1] == {"error": "max_children_exceeded"}


async def test_a_failing_child_is_isolated_from_its_parent() -> None:
    # Given one child whose model call fails outright
    llm = ScriptedLlm(
        {
            "review two pull requests": [
                _spawn_turn(
                    [
                        {"role": "logic-reviewer", "objective": "check the logic"},
                        {"role": "security-reviewer", "objective": "check the secrets"},
                    ]
                ),
                AssistantTurn(text=_PLAIN_FINDINGS),
            ],
            "check the logic": [AssistantTurn(text=_FINDINGS_JSON)],
            "check the secrets": [AssistantTurn(text=_PLAIN_FINDINGS)],
        },
        failures={"check the secrets": "gateway exploded"},
    )
    harness = _build(llm)

    # When the orchestrator runs
    result = await harness.runtime.run(
        agent_spec("orchestrator.pr"), task="review two pull requests", session_id=uuid4()
    )

    # Then the parent completes, surfacing the child failure as data
    assert result.status is AgentStatus.DONE
    assert [child.status for child in result.children] == [
        AgentStatus.DONE,
        AgentStatus.FAILED,
    ]
    failed = result.children[1]
    assert failed.error is not None and "gateway exploded" in failed.error

    failed_row = harness.store.row("security-reviewer")
    assert harness.store.finish_of(failed_row.run_id).status is AgentStatus.FAILED
    assert _types(harness.sink, failed_row.run_id) == [
        EventType.SPAWNED,
        EventType.STARTED,
        EventType.FAILED,
    ]
    assert harness.store.finish_of(result.run_id).status is AgentStatus.DONE


async def test_cancellation_propagates_from_parent_to_children() -> None:
    # Given cancellation trips while the orchestrator is mid-delegation
    flag = Flag()
    llm = ScriptedLlm(
        {
            "review two pull requests": [
                _spawn_turn(
                    [
                        {"role": "logic-reviewer", "objective": "check the logic"},
                        {"role": "security-reviewer", "objective": "check the secrets"},
                    ]
                ),
                AssistantTurn(text=_PLAIN_FINDINGS),
            ],
            "check the logic": [AssistantTurn(text=_PLAIN_FINDINGS)],
            "check the secrets": [AssistantTurn(text=_PLAIN_FINDINGS)],
        }
    )
    llm.hooks["review two pull requests"] = _set_true(flag)
    harness = _build(llm)

    # When the orchestrator runs
    result = await harness.runtime.run(
        agent_spec("orchestrator.pr"),
        task="review two pull requests",
        session_id=uuid4(),
        is_cancelled=flag,
    )

    # Then the parent and every child end cancelled, with no further model calls
    assert flag.value is True
    assert result.status is AgentStatus.CANCELLED
    assert [child.status for child in result.children] == [
        AgentStatus.CANCELLED,
        AgentStatus.CANCELLED,
    ]
    assert len(llm.calls_for("review two pull requests")) == 1
    assert llm.calls_for("check the logic") == []
    assert llm.calls_for("check the secrets") == []

    for row in harness.store.created:
        assert harness.store.finish_of(row.run_id).status is AgentStatus.CANCELLED
        assert _types(harness.sink, row.run_id)[-1] is EventType.CANCELLED


async def test_token_budget_stops_before_the_next_model_call() -> None:
    # Given a run whose budget is smaller than two of its turns
    read_tool = FakeTool("read_diff", result=["src/a.py"])
    tools: dict[str, Tool] = {"read_diff": read_tool}
    llm = ScriptedLlm(
        {
            "review the diff": [
                _read_turn("call-1", "src/a.py"),
                _read_turn("call-2", "src/b.py"),
                AssistantTurn(text=_FINDINGS_JSON, tokens=20, cost_usd=0.01),
            ]
        }
    )
    harness = _build(llm, policy=Policy(max_tokens_per_run=30), tools=tools)

    # When the orchestrator runs
    result = await harness.runtime.run(
        agent_spec("orchestrator.pr"), task="review the diff", session_id=uuid4()
    )

    # Then it stops before the third call and reports the partial run
    assert len(llm.calls_for("review the diff")) == 2
    assert result.status is AgentStatus.DONE
    assert result.tokens == 40
    assert "token budget" in result.summary
    assert harness.store.finish_of(result.run_id).status is AgentStatus.DONE
    assert _types(harness.sink, result.run_id)[-1] is EventType.COMPLETED
    assert len(read_tool.calls) == 2


async def test_the_step_limit_bounds_a_model_that_never_stops_calling_tools() -> None:
    # Given a policy that tightens the orchestrator's step cap to two
    read_tool = FakeTool("read_diff", result=["src/a.py"])
    tools: dict[str, Tool] = {"read_diff": read_tool}
    llm = ScriptedLlm(
        {
            "review the diff": [
                _read_turn("call-1", "src/a.py"),
                _read_turn("call-2", "src/b.py"),
                _read_turn("call-3", "src/c.py"),
            ]
        }
    )
    policy = Policy(
        max_steps_per_role={**Policy().max_steps_per_role, "orchestrator.pr": 2}
    )
    harness = _build(llm, policy=policy, tools=tools)

    # When the model keeps calling tools
    result = await harness.runtime.run(
        agent_spec("orchestrator.pr"), task="review the diff", session_id=uuid4()
    )

    # Then the loop stops at the cap, with a note, not an error
    assert len(llm.calls_for("review the diff")) == 2
    assert result.status is AgentStatus.DONE
    assert "step limit" in result.summary
    assert result.error is None
    assert _types(harness.sink, result.run_id).count(EventType.COMPLETED) == 1


async def test_session_run_ceiling_stops_before_spawning() -> None:
    # Given a session that only allows the orchestrator itself to run
    llm = ScriptedLlm(
        {
            "review the session": [
                _spawn_turn([{"role": "logic-reviewer", "objective": "check the logic"}]),
                AssistantTurn(text=_PLAIN_FINDINGS),
            ],
            "check the logic": [AssistantTurn(text=_PLAIN_FINDINGS)],
        }
    )
    harness = _build(llm, policy=Policy(max_total_agent_runs=1))

    # When it tries to delegate
    result = await harness.runtime.run(
        agent_spec("orchestrator.pr"), task="review the session", session_id=uuid4()
    )

    # Then the spawn is a budget stop, not an exception
    assert result.status is AgentStatus.DONE
    assert "agent-run ceiling" in result.summary
    assert result.children == []
    assert len(harness.store.created) == 1
    assert len(llm.calls_for("review the session")) == 1
    assert _payloads(harness.sink, result.run_id, EventType.TOOL_RESULT)[-1]["ok"] is False


async def test_credential_shaped_arguments_never_reach_a_payload() -> None:
    # Given a secret smuggled into a tool argument
    secret = "sk-" + "e" * 32
    llm = ScriptedLlm(
        {
            "review the change": [
                _spawn_turn([{"role": "ghost-reviewer", "objective": f"leak {secret}"}]),
                AssistantTurn(text=_PLAIN_FINDINGS),
            ]
        }
    )
    harness = _build(llm)

    # When the orchestrator runs
    await harness.runtime.run(
        agent_spec("orchestrator.pr"), task="review the change", session_id=uuid4()
    )

    # Then no payload carries it, and the redaction is visible in place
    dumped = [json.dumps(event.payload) for event in harness.sink.events]
    assert dumped
    for payload in dumped:
        assert secret not in payload
        assert "sk-" not in payload
    assert any("[redacted]" in payload for payload in dumped)


async def test_an_unresolvable_model_role_fails_only_that_run() -> None:
    # Given a workspace that never assigned a model to this role
    llm = ScriptedLlm({"review the diff": [AssistantTurn(text=_PLAIN_FINDINGS)]})
    store = RecordingStore()
    runtime = AgentRuntime(
        llm=llm,
        store=store,
        bus=EventBus(CollectingSink()),
        policy=Policy(),
        resolver=StaticResolver({}),
    )

    # When the run is attempted
    result = await runtime.run(
        agent_spec("logic-reviewer"), task="review the diff", session_id=uuid4()
    )

    # Then it fails typed, with no raise and no half-built run row
    assert result.status is AgentStatus.FAILED
    assert result.error is not None and "review.specialist" in result.error
    assert store.created == []


@pytest.mark.parametrize("role", ["orchestrator.main", "orchestrator.pr"])
async def test_orchestrators_offer_spawn_and_read_tools(role: str) -> None:
    # Given an orchestrator whose whole allow-list is injected
    llm = ScriptedLlm({"do the work": [AssistantTurn(text=_PLAIN_FINDINGS)]})
    harness = _build(llm, tools=_read_tools())
    spec: AgentSpec = agent_spec(role)

    # When it runs at the top of the tree
    await harness.runtime.run(spec, task="do the work", session_id=uuid4())

    # Then the model was offered exactly the spec's allow-list, in order
    offered = [tool_spec.name for tool_spec in llm.calls_for("do the work")[0].tools]
    assert offered == spec.tools
    assert SPAWN_TOOL in offered


class _RunProbe(ScriptedLlm):
    """A scripted model that also reports the run it was called inside."""

    def __init__(self, script: Mapping[str, list[AssistantTurn]]) -> None:
        super().__init__(script)
        self.seen: list[RunRef | None] = []

    async def complete(
        self, *, model_id: str, messages: list[Message], tools: list[ToolSpec]
    ) -> AssistantTurn:
        self.seen.append(current_run())
        return await super().complete(model_id=model_id, messages=messages, tools=tools)


async def test_a_run_publishes_itself_to_the_calls_made_inside_it() -> None:
    # Given an agent whose model call happens deep inside the run
    llm = _RunProbe({"review the diff": [AssistantTurn(text=_PLAIN_FINDINGS)]})
    harness = _build(llm)
    session_id, target_id = uuid4(), uuid4()

    # When it runs
    await harness.runtime.run(
        agent_spec("reviewer"),
        task="review the diff",
        session_id=session_id,
        target_id=target_id,
        depth=2,
    )

    # Then the call could name the run that made it (spec v2 11.3)
    row = harness.store.row("reviewer")
    assert len(llm.seen) == 1
    ref = llm.seen[0]
    assert ref is not None
    assert ref.run_id == row.run_id
    assert ref.session_id == session_id
    assert ref.target_id == target_id
    assert ref.level is HarnessLevel.SUB
    assert ref.role == "reviewer"
    assert ref.model_id == "model-review"

    # And nothing stays bound once the run returns
    assert current_run() is None
