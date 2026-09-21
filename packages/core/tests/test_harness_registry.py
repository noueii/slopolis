"""Tests for the built-in Harness V1 agent registry (spec v2 §3, §10)."""

from collections.abc import Mapping
from typing import cast

import pytest

from slopolis_core.harness import (
    BUILT_IN_AGENTS,
    READ_ONLY_TOOLS,
    SPAWN_TOOL,
    AgentSpec,
    HarnessLevel,
    UnknownRoleError,
    agent_spec,
    model_roles,
)
from slopolis_core.roles import ASSIGNABLE_ROLES

#: The spec §3 table: role -> (level, model role, spawns).
_EXPECTED: dict[str, tuple[HarnessLevel, str, bool]] = {
    "orchestrator.main": (HarnessLevel.MAIN, "harness.orchestrator", True),
    "orchestrator.pr": (HarnessLevel.PR, "harness.orchestrator", True),
    "reviewer": (HarnessLevel.SUB, "review", False),
    "context-gatherer": (HarnessLevel.SUB, "review.fast", False),
    "logic-reviewer": (HarnessLevel.SUB, "review.specialist", False),
    "security-reviewer": (HarnessLevel.SUB, "review.security", False),
    "test-reviewer": (HarnessLevel.SUB, "review.tests", False),
}


def test_registry_contains_exactly_the_spec_section_3_roles() -> None:
    assert set(BUILT_IN_AGENTS) == set(_EXPECTED)
    assert isinstance(BUILT_IN_AGENTS, Mapping)
    for role, (level, model_role, can_spawn) in _EXPECTED.items():
        spec = BUILT_IN_AGENTS[role]

        assert spec.name == role
        assert spec.level is level
        assert spec.model_role == model_role
        assert spec.can_spawn is can_spawn


def test_orchestrators_delegate_and_reviewers_only_read() -> None:
    for spec in BUILT_IN_AGENTS.values():
        allowed = {SPAWN_TOOL, *READ_ONLY_TOOLS} if spec.can_spawn else set(READ_ONLY_TOOLS)

        assert set(spec.tools) == allowed
        assert (SPAWN_TOOL in spec.tools) is spec.can_spawn


def test_every_prompt_states_the_json_contract_and_read_only_posture() -> None:
    prompts: set[str] = set()
    for spec in BUILT_IN_AGENTS.values():
        assert "JSON" in spec.system_prompt
        assert "findings" in spec.system_prompt
        assert "never write" in spec.system_prompt
        assert spec.system_prompt not in prompts
        prompts.add(spec.system_prompt)

    assert len(prompts) == len(_EXPECTED)


def test_step_caps_match_the_policy_table() -> None:
    assert BUILT_IN_AGENTS["orchestrator.main"].max_steps == 12
    assert BUILT_IN_AGENTS["orchestrator.pr"].max_steps == 12
    for role in ("context-gatherer", "logic-reviewer", "security-reviewer", "test-reviewer"):
        assert BUILT_IN_AGENTS[role].max_steps == 6

    # The v1 reviewer has no per-aspect key in ``Policy``; its cap is spec §6's
    # plain "reviewers 6", which a tightened workspace policy lowers via ``min``.
    assert BUILT_IN_AGENTS["reviewer"].max_steps == 6


def test_agent_spec_returns_the_record_and_rejects_unknown_roles() -> None:
    assert agent_spec("logic-reviewer") is BUILT_IN_AGENTS["logic-reviewer"]

    with pytest.raises(UnknownRoleError) as caught:
        agent_spec("ghost-reviewer")

    assert caught.value.role == "ghost-reviewer"
    assert "ghost-reviewer" in str(caught.value)


def test_model_roles_are_the_distinct_roles_the_agents_reference() -> None:
    roles = model_roles()

    assert roles == (
        "harness.orchestrator",
        "review",
        "review.fast",
        "review.specialist",
        "review.security",
        "review.tests",
    )
    assert len(roles) == len(set(roles))
    assert set(roles) <= set(ASSIGNABLE_ROLES)
    assert set(roles) == {spec.model_role for spec in BUILT_IN_AGENTS.values()}


def test_the_registry_mapping_cannot_be_mutated() -> None:
    mutable = cast("dict[str, AgentSpec]", BUILT_IN_AGENTS)
    spec = BUILT_IN_AGENTS["logic-reviewer"]

    with pytest.raises(TypeError):
        mutable["logic-reviewer"] = spec
