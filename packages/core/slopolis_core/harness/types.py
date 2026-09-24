"""Runtime types for the Harness V1 supervisor tree (spec §3-§7).

These are the harness's durable domain shapes: the enum vocabulary, the spawn
contract, the agent registry record, the policy bounds, the run tree, and the
event log. Every model is Pydantic v2 with ``extra="forbid"`` so an unknown
field is a hard error at the boundary, and none of them name a model or
provider — a role resolves its model through the workspace
``ModelAssignment`` (spec §3).

Wire values mirror ``apps/web/src/api/contract.ts`` one-to-one, so the UI
contract and the backend domain cannot drift.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from slopolis_core.findings import Finding

__all__ = [
    "AgentEvent",
    "AgentSpec",
    "AgentStatus",
    "Aspect",
    "EventType",
    "HarnessLevel",
    "Policy",
    "RunNode",
    "Scope",
    "SubAgentResult",
    "SubtaskSpec",
]


class HarnessLevel(StrEnum):
    """Depth of an agent in the run tree (spec §2)."""

    MAIN = "main"
    PR = "pr"
    SUB = "sub"


class AgentStatus(StrEnum):
    """Lifecycle of one agent run; values match ``contract.ts``."""

    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Aspect(StrEnum):
    """Review aspect a scoped sub-task targets (spec §3-§4).

    Derived from the built-in review roles: ``context-gatherer`` → ``context``,
    ``logic-reviewer`` → ``logic``, ``security-reviewer`` → ``security``,
    ``test-reviewer`` → ``tests``. ``Scope.aspect`` lets an orchestrator route
    or skip an aspect without hard-coding a role name.
    """

    CONTEXT = "context"
    LOGIC = "logic"
    SECURITY = "security"
    TESTS = "tests"


class EventType(StrEnum):
    """Wire types for every persisted harness event (spec §7)."""

    SPAWNED = "agent.spawned"
    STARTED = "agent.started"
    STEP = "agent.step"
    TOOL_CALL = "agent.tool_call"
    TOOL_RESULT = "agent.tool_result"
    MESSAGE = "agent.message"
    #: One real gateway call: the request messages, the raw response, and what it
    #: cost. The only event that carries a transcript rather than a digest.
    TURN = "agent.turn"
    FINDING = "agent.finding"
    COMPLETED = "agent.completed"
    FAILED = "agent.failed"
    CANCELLED = "agent.cancelled"


class _HarnessModel(BaseModel):
    """Base with the harness-wide strictness: unknown fields are rejected."""

    model_config = ConfigDict(extra="forbid")


#: Per-role tool-loop step caps (spec §6): orchestrators get 12, reviewers 6.
_DEFAULT_MAX_STEPS: dict[str, int] = {
    "orchestrator.main": 12,
    "orchestrator.pr": 12,
    "context-gatherer": 6,
    "logic-reviewer": 6,
    "security-reviewer": 6,
    "test-reviewer": 6,
}


def _default_max_steps() -> dict[str, int]:
    """Return a fresh copy of the per-role step caps so instances never alias."""
    return dict(_DEFAULT_MAX_STEPS)


class Scope(_HarnessModel):
    """The slice of work one sub-task is allowed to touch (spec §4)."""

    target_id: UUID | None = None
    paths: list[str] = []
    aspect: Aspect | None = None
    constraints: list[str] = []


class SubtaskSpec(_HarnessModel):
    """One batched unit of delegated work handed to ``spawn_subagents`` (spec §4)."""

    role: str
    objective: str
    scope: Scope = Scope()
    model_role: str | None = None


class SubAgentResult(_HarnessModel):
    """Typed outcome of one child run, returned to the spawning orchestrator."""

    run_id: UUID
    role: str
    status: AgentStatus
    summary: str
    findings: list[Finding] = []
    tokens_used: int = 0
    cost_usd: float = 0.0
    error: str | None = None


class AgentSpec(_HarnessModel):
    """Registry record for one built-in role (spec §3)."""

    name: str
    level: HarnessLevel
    model_role: str
    system_prompt: str
    tools: list[str]
    max_steps: int
    can_spawn: bool


class Policy(_HarnessModel):
    """Session-wide bounds; defaults are spec §6 and may only be tightened."""

    max_depth: int = 2
    max_concurrent_pr_orchestrators: int = 4
    max_children_per_orchestrator: int = 12
    max_concurrent_children: int = 4
    max_total_agent_runs: int = 64
    max_tokens_per_run: int = 60_000
    max_cost_usd_per_run: float = 0.40
    max_session_cost_usd: float = 5.00
    max_wallclock_s: int = 900
    max_steps_per_role: dict[str, int] = Field(default_factory=_default_max_steps)


class AgentEvent(_HarnessModel):
    """One append-only run event and the source of truth for replay (spec §7)."""

    id: UUID
    run_id: UUID
    seq: int
    type: EventType
    payload: dict[str, object]
    created_at: datetime


class RunNode(_HarnessModel):
    """One node of the persisted run tree returned by the tree endpoint (spec §7)."""

    id: UUID
    session_id: UUID
    target_id: UUID | None
    parent_run_id: UUID | None
    level: HarnessLevel
    role: str
    model_id: str | None
    objective: str
    status: AgentStatus
    tokens: int
    cost_usd: float
    started_at: datetime | None
    ended_at: datetime | None
    children: list[RunNode] = []
