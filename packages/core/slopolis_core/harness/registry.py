"""Built-in Harness V1 agent registry (spec v2 §3, §10).

The registry is the only place a role name is bound to its level, its model
role, its read-only tool allow-list, its prompt, and its step cap. Models stay
out of the harness: a spec names a *model role*, and the workspace resolves that
role to a concrete model through ``ModelAssignment`` (spec 10.2), so no model
name ever appears in code.

``model_roles()`` exists for the worker: it pre-resolves the exact set of roles
the tree will ask for, so a missing assignment fails before a run starts rather
than inside one.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from slopolis_core.harness.types import AgentSpec, HarnessLevel, Policy
from slopolis_core.roles import REVIEW_ROLE

__all__ = [
    "BUILT_IN_AGENTS",
    "MAIN_AGENT",
    "PR_AGENT",
    "READ_ONLY_TOOLS",
    "SPAWN_TOOL",
    "UnknownRoleError",
    "agent_spec",
    "model_roles",
]

#: Names of the two orchestrator roles. Exported so a caller that records a run
#: row without going through the registry — the server's submit path creates the
#: session's ``main`` run (spec §15) — names the role from one place instead of
#: repeating the string literal.
MAIN_AGENT = "orchestrator.main"
PR_AGENT = "orchestrator.pr"

#: The read-only tool vocabulary (spec §2, §10): no clone, no exec, no write.
READ_ONLY_TOOLS: tuple[str, ...] = (
    "read_file",
    "list_dir",
    "read_diff",
    "search_symbol",
    "read_issue",
)

#: The one tool that creates child runs; offered only where ``can_spawn`` (spec §4).
SPAWN_TOOL = "spawn_subagents"

#: Tools at levels 0-1: delegate, then read what was delegated to (spec §2).
_ORCHESTRATOR_TOOLS: tuple[str, ...] = (SPAWN_TOOL, *READ_ONLY_TOOLS)

#: Tools at level 2: read only — a sub-agent cannot delegate (spec §2).
_REVIEWER_TOOLS: tuple[str, ...] = READ_ONLY_TOOLS

#: Step caps come from the ``Policy`` table (spec §6) instead of a second copy,
#: so a tightened workspace policy and the registry cannot drift apart.
_STEP_CAPS: Mapping[str, int] = MappingProxyType(Policy().max_steps_per_role)

#: Spec §6 groups the v1 reviewer with the aspect reviewers at 6 steps, but
#: ``Policy.max_steps_per_role`` lists the aspect roles, not this one. A tightened
#: workspace policy still lowers it: the runtime takes the ``min`` of this cap and
#: the policy's cap for the role.
_V1_REVIEWER_STEPS = 6

_MAIN_PROMPT = (
    "You are the main orchestrator for one review session. Delegate: spawn one PR "
    "orchestrator per pull-request target with spawn_subagents, then aggregate what "
    "they return into one deduplicated findings set and state coverage and limitations.\n"
    'Return your findings as a JSON object of the shape {"findings": [...]}.\n'
    "Use only the tools you are given; you never write, edit, run, or post code."
)

_PR_PROMPT = (
    "You are the PR orchestrator for exactly one pull request. Read what you need, "
    "decide which review aspects matter, spawn one reviewer sub-agent per aspect with "
    "spawn_subagents, then merge and deduplicate their findings.\n"
    'Return your findings as a JSON object of the shape {"findings": [...]}.\n'
    "Use only the read-only tools you are given; you never write, edit, run, or post code."
)

_REVIEWER_PROMPT = (
    "You are the reviewer sub-agent for one pull request. Review that pull request and "
    "report what is wrong with it: correctness, security, edge cases, and coverage.\n"
    'Return your findings as a JSON object of the shape {"findings": [...]}.\n'
    "Use only the read-only tools you are given; you never write, edit, or run code."
)

_CONTEXT_PROMPT = (
    "You are the context sub-agent for one scoped objective. Gather the files, symbols, "
    "and call paths a reviewer needs in order to judge this change, and report what is "
    "missing or unclear.\n"
    'Return your findings as a JSON object of the shape {"findings": [...]}.\n'
    "Use only the read-only tools you are given; you never write, edit, or run code."
)

_LOGIC_PROMPT = (
    "You are the logic sub-agent for one scoped objective. Judge control flow, edge "
    "cases, error handling, and contract correctness in the changed code.\n"
    'Return your findings as a JSON object of the shape {"findings": [...]}.\n'
    "Use only the read-only tools you are given; you never write, edit, or run code."
)

_SECURITY_PROMPT = (
    "You are the security sub-agent for one scoped objective. Hunt for injection, "
    "authorization gaps, secret exposure, and unsafe handling of untrusted input in "
    "the changed code.\n"
    'Return your findings as a JSON object of the shape {"findings": [...]}.\n'
    "Use only the read-only tools you are given; you never write, edit, or run code."
)

_TESTS_PROMPT = (
    "You are the test sub-agent for one scoped objective. Judge whether the change is "
    "actually covered: missing cases, weak assertions, and untested paths.\n"
    'Return your findings as a JSON object of the shape {"findings": [...]}.\n'
    "Use only the read-only tools you are given; you never write, edit, or run code."
)


def _build() -> Mapping[str, AgentSpec]:
    """Assemble the spec table; orchestrators delegate, reviewers only read."""
    specs = {
        MAIN_AGENT: AgentSpec(
            name=MAIN_AGENT,
            level=HarnessLevel.MAIN,
            model_role="harness.orchestrator",
            system_prompt=_MAIN_PROMPT,
            tools=list(_ORCHESTRATOR_TOOLS),
            max_steps=_STEP_CAPS[MAIN_AGENT],
            can_spawn=True,
        ),
        PR_AGENT: AgentSpec(
            name=PR_AGENT,
            level=HarnessLevel.PR,
            model_role="harness.orchestrator",
            system_prompt=_PR_PROMPT,
            tools=list(_ORCHESTRATOR_TOOLS),
            max_steps=_STEP_CAPS[PR_AGENT],
            can_spawn=True,
        ),
        "reviewer": AgentSpec(
            name="reviewer",
            level=HarnessLevel.SUB,
            model_role=REVIEW_ROLE,
            system_prompt=_REVIEWER_PROMPT,
            tools=list(_REVIEWER_TOOLS),
            max_steps=_V1_REVIEWER_STEPS,
            can_spawn=False,
        ),
        "context-gatherer": AgentSpec(
            name="context-gatherer",
            level=HarnessLevel.SUB,
            model_role="review.fast",
            system_prompt=_CONTEXT_PROMPT,
            tools=list(_REVIEWER_TOOLS),
            max_steps=_STEP_CAPS["context-gatherer"],
            can_spawn=False,
        ),
        "logic-reviewer": AgentSpec(
            name="logic-reviewer",
            level=HarnessLevel.SUB,
            model_role="review.specialist",
            system_prompt=_LOGIC_PROMPT,
            tools=list(_REVIEWER_TOOLS),
            max_steps=_STEP_CAPS["logic-reviewer"],
            can_spawn=False,
        ),
        "security-reviewer": AgentSpec(
            name="security-reviewer",
            level=HarnessLevel.SUB,
            model_role="review.security",
            system_prompt=_SECURITY_PROMPT,
            tools=list(_REVIEWER_TOOLS),
            max_steps=_STEP_CAPS["security-reviewer"],
            can_spawn=False,
        ),
        "test-reviewer": AgentSpec(
            name="test-reviewer",
            level=HarnessLevel.SUB,
            model_role="review.tests",
            system_prompt=_TESTS_PROMPT,
            tools=list(_REVIEWER_TOOLS),
            max_steps=_STEP_CAPS["test-reviewer"],
            can_spawn=False,
        ),
    }
    return MappingProxyType(specs)


#: Every built-in role, keyed by the name a spawn request must use (spec §3).
BUILT_IN_AGENTS: Mapping[str, AgentSpec] = _build()

#: The distinct model roles the built-ins reference, in registry order.
_MODEL_ROLES: tuple[str, ...] = tuple(
    dict.fromkeys(spec.model_role for spec in BUILT_IN_AGENTS.values())
)


class UnknownRoleError(KeyError):
    """Raised when a spawn or a lookup names a role that is not built in."""

    def __init__(self, role: str) -> None:
        super().__init__(role)
        self.role = role

    def __str__(self) -> str:
        return f"unknown agent role {self.role!r}"


def agent_spec(role: str) -> AgentSpec:
    """Return the built-in spec for ``role``; raise :class:`UnknownRoleError` otherwise."""
    try:
        return BUILT_IN_AGENTS[role]
    except KeyError as exc:
        raise UnknownRoleError(role) from exc


def model_roles() -> tuple[str, ...]:
    """Return every model role the built-in agents resolve, without repetition."""
    return _MODEL_ROLES
