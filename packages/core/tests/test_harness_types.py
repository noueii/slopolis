"""Tests for Harness V1 runtime types (plan Task 1.1, spec v2 §3-§7)."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from slopolis_core.domain import Severity
from slopolis_core.findings import Finding
from slopolis_core.harness import (
    AgentEvent,
    AgentSpec,
    AgentStatus,
    Aspect,
    EventType,
    HarnessLevel,
    Policy,
    RunNode,
    Scope,
    SubAgentResult,
    SubtaskSpec,
)


def _at() -> datetime:
    """A fixed, timezone-aware timestamp for round-trip comparisons."""
    return datetime(2026, 9, 13, 12, 0, tzinfo=UTC)


def _finding() -> Finding:
    return Finding(
        path="src/a.py",
        line=3,
        severity=Severity.ERROR,
        category="correctness",
        message="boom",
        confidence=0.8,
    )


def _run_node(
    *,
    session_id: UUID,
    node_id: UUID,
    level: HarnessLevel,
    role: str,
    target_id: UUID | None = None,
    parent_run_id: UUID | None = None,
    model_id: str | None = None,
    children: list[RunNode] | None = None,
) -> RunNode:
    """Build a run node with explicit nulls so round-trips exercise optionals."""
    return RunNode(
        id=node_id,
        session_id=session_id,
        target_id=target_id,
        parent_run_id=parent_run_id,
        level=level,
        role=role,
        model_id=model_id,
        objective="do the thing",
        status=AgentStatus.PENDING,
        tokens=0,
        cost_usd=0.0,
        started_at=None,
        ended_at=None,
        children=[] if children is None else children,
    )


def test_enum_wire_values() -> None:
    assert [level.value for level in HarnessLevel] == ["main", "pr", "sub"]
    assert {status.value for status in AgentStatus} == {
        "pending", "running", "done", "failed", "cancelled",
    }
    assert {aspect.value for aspect in Aspect} == {"context", "logic", "security", "tests"}
    assert {event.value for event in EventType} == {
        "agent.spawned", "agent.started", "agent.step", "agent.tool_call",
        "agent.tool_result", "agent.message", "agent.turn", "agent.finding",
        "agent.completed", "agent.failed", "agent.cancelled",
    }


def test_policy_defaults_match_spec_section_6() -> None:
    policy = Policy()

    assert policy.max_depth == 2
    assert policy.max_concurrent_pr_orchestrators == 4
    assert policy.max_children_per_orchestrator == 12
    assert policy.max_concurrent_children == 4
    assert policy.max_total_agent_runs == 64
    assert policy.max_tokens_per_run == 60_000
    assert policy.max_cost_usd_per_run == 0.40
    assert policy.max_session_cost_usd == 5.00
    assert policy.max_wallclock_s == 900
    assert policy.max_steps_per_role["orchestrator.main"] == 12
    assert policy.max_steps_per_role["orchestrator.pr"] == 12
    assert policy.max_steps_per_role["context-gatherer"] == 6
    assert policy.max_steps_per_role["logic-reviewer"] == 6


def test_policy_max_steps_default_is_not_shared() -> None:
    first = Policy()
    first.max_steps_per_role["orchestrator.main"] = 1

    assert Policy().max_steps_per_role["orchestrator.main"] == 12


def test_scope_roundtrip_defaults() -> None:
    scope = Scope()
    restored = Scope.model_validate_json(scope.model_dump_json())

    assert restored == scope
    assert restored.target_id is None and restored.aspect is None
    assert restored.paths == [] and restored.constraints == []


def test_scope_roundtrip_with_optionals() -> None:
    scope = Scope(
        target_id=uuid4(),
        paths=["src/a.py"],
        aspect=Aspect.SECURITY,
        constraints=["do not flag style"],
    )
    restored = Scope.model_validate_json(scope.model_dump_json())

    assert restored == scope
    assert restored.aspect is Aspect.SECURITY


def test_subtask_spec_roundtrip_defaults() -> None:
    spec = SubtaskSpec(role="logic-reviewer", objective="review the diff")
    restored = SubtaskSpec.model_validate_json(spec.model_dump_json())

    assert restored == spec
    assert restored.scope == Scope()
    assert restored.model_role is None


def test_subtask_spec_roundtrip_with_overrides() -> None:
    spec = SubtaskSpec(
        role="logic-reviewer",
        objective="review the diff",
        scope=Scope(paths=["src/a.py"], aspect=Aspect.LOGIC),
        model_role="review.fast",
    )
    restored = SubtaskSpec.model_validate_json(spec.model_dump_json())

    assert restored == spec
    assert restored.model_role == "review.fast"


def test_agent_spec_roundtrip() -> None:
    spec = AgentSpec(
        name="orchestrator.pr",
        level=HarnessLevel.PR,
        model_role="harness.orchestrator",
        system_prompt="review one PR",
        tools=["spawn_subagents", "read_diff"],
        max_steps=12,
        can_spawn=True,
    )
    restored = AgentSpec.model_validate_json(spec.model_dump_json())

    assert restored == spec
    assert restored.level is HarnessLevel.PR


def test_subagent_result_roundtrip_minimal() -> None:
    result = SubAgentResult(
        run_id=uuid4(),
        role="logic-reviewer",
        status=AgentStatus.DONE,
        summary="no issues",
    )
    restored = SubAgentResult.model_validate_json(result.model_dump_json())

    assert restored == result
    assert restored.findings == []
    assert restored.tokens_used == 0 and restored.cost_usd == 0.0
    assert restored.error is None


def test_subagent_result_roundtrip_with_findings_and_error() -> None:
    result = SubAgentResult(
        run_id=uuid4(),
        role="logic-reviewer",
        status=AgentStatus.FAILED,
        summary="partial coverage",
        findings=[_finding()],
        tokens_used=1234,
        cost_usd=0.12,
        error="budget exceeded",
    )
    restored = SubAgentResult.model_validate_json(result.model_dump_json())

    assert restored == result
    assert restored.status is AgentStatus.FAILED
    assert restored.findings[0].severity is Severity.ERROR
    assert restored.error == "budget exceeded"


def test_agent_event_roundtrip() -> None:
    event = AgentEvent(
        id=uuid4(),
        run_id=uuid4(),
        seq=3,
        type=EventType.TOOL_CALL,
        payload={"tool": "read_file", "arg_count": 2, "ok": True},
        created_at=_at(),
    )
    restored = AgentEvent.model_validate_json(event.model_dump_json())

    assert restored == event
    assert restored.type is EventType.TOOL_CALL
    assert restored.created_at == _at()


def test_run_node_roundtrip_without_optionals() -> None:
    node = _run_node(
        session_id=uuid4(),
        node_id=uuid4(),
        level=HarnessLevel.MAIN,
        role="orchestrator.main",
    )
    restored = RunNode.model_validate_json(node.model_dump_json())

    assert restored == node
    assert restored.children == []
    assert restored.started_at is None and restored.ended_at is None


def test_run_node_nesting_roundtrip() -> None:
    session_id = uuid4()
    main_id, pr_id, sub_id, target_id = uuid4(), uuid4(), uuid4(), uuid4()
    sub = _run_node(
        session_id=session_id,
        node_id=sub_id,
        level=HarnessLevel.SUB,
        role="logic-reviewer",
        target_id=target_id,
        parent_run_id=pr_id,
        model_id="gpt-fast",
    )
    pr = _run_node(
        session_id=session_id,
        node_id=pr_id,
        level=HarnessLevel.PR,
        role="orchestrator.pr",
        target_id=target_id,
        parent_run_id=main_id,
        children=[sub],
    )
    main = _run_node(
        session_id=session_id,
        node_id=main_id,
        level=HarnessLevel.MAIN,
        role="orchestrator.main",
        children=[pr],
    )
    restored = RunNode.model_validate_json(main.model_dump_json())

    assert restored == main
    assert restored.children[0].level is HarnessLevel.PR
    assert restored.children[0].children[0].level is HarnessLevel.SUB
    assert restored.children[0].children[0].parent_run_id == pr_id


def test_extra_fields_rejected() -> None:
    scope_payload = Scope().model_dump()
    scope_payload["bogus"] = True
    policy_payload = Policy().model_dump()
    policy_payload["bogus"] = True
    event = AgentEvent(
        id=uuid4(),
        run_id=uuid4(),
        seq=1,
        type=EventType.STARTED,
        payload={},
        created_at=_at(),
    )
    event_payload = event.model_dump()
    event_payload["bogus"] = True

    cases: list[tuple[type[Scope] | type[Policy] | type[AgentEvent], dict[str, object]]] = [
        (Scope, scope_payload),
        (Policy, policy_payload),
        (AgentEvent, event_payload),
    ]
    for model, payload in cases:
        with pytest.raises(ValidationError):
            model.model_validate(payload)
