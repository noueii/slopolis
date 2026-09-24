"""Harness V1 registry, model resolution, runtime, types, and events (spec v2 §3-§7).

Public surface is re-exported here so importers depend on
``slopolis_core.harness`` rather than the internal module layout.
"""

from slopolis_core.harness.context import RunRef, bind_run, current_run
from slopolis_core.harness.events import (
    CallbackSink,
    CollectingSink,
    EventBus,
    EventCallback,
    EventSink,
    redact,
)
from slopolis_core.harness.registry import (
    BUILT_IN_AGENTS,
    MAIN_AGENT,
    PR_AGENT,
    READ_ONLY_TOOLS,
    SPAWN_TOOL,
    UnknownRoleError,
    agent_spec,
    model_roles,
)
from slopolis_core.harness.resolver import (
    ModelChoice,
    ModelResolutionError,
    ModelResolver,
    StaticResolver,
)
from slopolis_core.harness.runtime import (
    AgentResult,
    AgentRuntime,
    AssistantTurn,
    LlmTurn,
    Message,
    RunStore,
    Tool,
    ToolCall,
    ToolSpec,
)
from slopolis_core.harness.types import (
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

__all__ = [
    "BUILT_IN_AGENTS",
    "MAIN_AGENT",
    "PR_AGENT",
    "READ_ONLY_TOOLS",
    "SPAWN_TOOL",
    "AgentEvent",
    "AgentResult",
    "AgentRuntime",
    "AgentSpec",
    "AgentStatus",
    "Aspect",
    "AssistantTurn",
    "CallbackSink",
    "CollectingSink",
    "EventBus",
    "EventCallback",
    "EventSink",
    "EventType",
    "HarnessLevel",
    "LlmTurn",
    "Message",
    "ModelChoice",
    "ModelResolutionError",
    "ModelResolver",
    "Policy",
    "RunNode",
    "RunRef",
    "RunStore",
    "Scope",
    "StaticResolver",
    "SubAgentResult",
    "SubtaskSpec",
    "Tool",
    "ToolCall",
    "ToolSpec",
    "UnknownRoleError",
    "agent_spec",
    "bind_run",
    "current_run",
    "model_roles",
    "redact",
]
