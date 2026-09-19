"""Event bus and redaction layer for the Harness V1 run tree (plan Task 1.3).

Spec v2 §7 makes the persisted event log the single source of truth for the run
tree, and §10 requires that *secrets never enter events*: payloads are built
from typed fields, never raw model output.

This module owns both halves of that contract:

* :func:`redact` reconstructs a payload from an explicit per-``EventType``
  allow-list, keeps only scalar values, masks credential-shaped strings, and
  truncates oversized strings.
* :class:`EventBus` assigns a monotonic ``seq`` per ``run_id``, redacts, builds
  the :class:`~slopolis_core.harness.types.AgentEvent`, and fans it out to every
  registered sink in order.

``packages/core`` must not depend on ``packages/db`` or the server, so sinks are
defined here as a :class:`EventSink` protocol and the persistence/SSE adapters
are injected (see :class:`CallbackSink`).
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from typing import Final, Protocol, runtime_checkable
from uuid import UUID, uuid4

from slopolis_core.harness.types import AgentEvent, EventType

__all__ = [
    "CallbackSink",
    "CollectingSink",
    "EventBus",
    "EventCallback",
    "EventSink",
    "redact",
]

#: Fixed replacement written in place of any credential-shaped substring.
_REDACTION_PLACEHOLDER: Final = "[redacted]"

#: Hard cap on any single string field; longer values are truncated (spec §10).
_MAX_STRING_LEN: Final = 2000

#: Suffix that marks a value as truncated, so truncation is visible downstream.
_TRUNCATION_MARKER: Final = "...[truncated]"

#: Credential shapes common enough to be worth masking by value, not just by key.
_SECRET_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(
        r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
        re.DOTALL,
    ),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"\bsk_(live|test)_[A-Za-z0-9]{8,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{8,}"),
    re.compile(r"\bAKIA[0-9A-Z]{12,}"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{8,}"),
)

#: Per-type allow-list. A payload key absent here never reaches a sink.
_ALLOWED_KEYS: Final[dict[EventType, frozenset[str]]] = {
    EventType.SPAWNED: frozenset(
        {"role", "level", "parent_run_id", "target_id", "objective", "model_role", "depth"}
    ),
    EventType.STARTED: frozenset({"role", "model_id", "status"}),
    EventType.STEP: frozenset({"step", "model_id", "summary", "tool_call_count"}),
    EventType.TOOL_CALL: frozenset({"tool", "args_summary", "step", "call_id"}),
    EventType.TOOL_RESULT: frozenset(
        {"tool", "ok", "summary", "step", "call_id", "result_count"}
    ),
    EventType.MESSAGE: frozenset({"role", "summary", "chars"}),
    EventType.FINDING: frozenset(
        {"path", "line", "severity", "category", "message", "suggestion", "confidence"}
    ),
    EventType.COMPLETED: frozenset(
        {"status", "summary", "tokens_used", "cost_usd", "finding_count", "step_count"}
    ),
    EventType.FAILED: frozenset({"status", "error", "summary", "tokens_used", "step_count"}),
    EventType.CANCELLED: frozenset({"status", "summary", "step_count"}),
}


def _union_of_allowed() -> frozenset[str]:
    """Return every key allow-listed for at least one event type."""
    keys: set[str] = set()
    for allowed in _ALLOWED_KEYS.values():
        keys.update(allowed)
    return frozenset(keys)


_UNION_KEYS: Final[frozenset[str]] = _union_of_allowed()

#: Sentinel marking a value that must not survive redaction.
_DROP: Final = object()


def _mask_secrets(value: str) -> str:
    """Replace every credential-shaped substring with the fixed placeholder."""
    masked = value
    for pattern in _SECRET_PATTERNS:
        masked = pattern.sub(_REDACTION_PLACEHOLDER, masked)
    return masked


def _truncate(value: str) -> str:
    """Clamp a string to the hard cap and mark it; idempotent on replay."""
    if len(value) <= _MAX_STRING_LEN or value.endswith(_TRUNCATION_MARKER):
        return value
    return value[:_MAX_STRING_LEN] + _TRUNCATION_MARKER


def _redact_value(value: object) -> object:
    """Return a safe scalar for an allow-listed key, or :data:`_DROP`.

    Non-scalars (dicts, lists, model turns, transcripts) are dropped outright:
    only strings, bools, ints, floats, and ``None`` can be typed fields here.
    """
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _truncate(_mask_secrets(value))
    return _DROP


def redact(
    payload: Mapping[str, object], *, event_type: EventType | None = None
) -> dict[str, object]:
    """Build a safe event payload from allow-listed typed fields only.

    ``event_type`` selects the per-type allow-list; when omitted, the union of
    all allow-listed keys is used. Keys outside the allow-list, and values that
    are not scalars, are dropped. Strings are masked for credential shapes and
    truncated to a hard cap. Raw model output, transcripts, and unknown fields
    can therefore never reach an event.
    """
    allowed = _UNION_KEYS if event_type is None else _ALLOWED_KEYS[event_type]
    safe: dict[str, object] = {}
    for key, value in payload.items():
        if key not in allowed:
            continue
        cleaned = _redact_value(value)
        if cleaned is _DROP:
            continue
        safe[key] = cleaned
    return safe


#: Async callback signature accepted by :class:`CallbackSink`.
type EventCallback = Callable[[AgentEvent], Awaitable[None]]


@runtime_checkable
class EventSink(Protocol):
    """A destination for emitted events (persistence, SSE, tests)."""

    async def emit(self, event: AgentEvent) -> None:
        """Deliver one event to this sink."""
        ...


class CollectingSink:
    """In-memory sink used by tests and replay assertions."""

    def __init__(self) -> None:
        self._events: list[AgentEvent] = []

    @property
    def events(self) -> list[AgentEvent]:
        """A snapshot copy of everything emitted so far, in arrival order."""
        return list(self._events)

    def __len__(self) -> int:
        return len(self._events)

    async def emit(self, event: AgentEvent) -> None:
        """Append the event to the in-memory log."""
        self._events.append(event)


class CallbackSink:
    """Sink that forwards each event to an injected async callback.

    This is how the worker persists to the DB and how the server pushes SSE:
    the adapter lives outside ``core``, so ``core`` never imports ``slopolis_db``
    or ``fastapi``.
    """

    def __init__(self, callback: EventCallback) -> None:
        self._callback = callback

    async def emit(self, event: AgentEvent) -> None:
        """Invoke the injected callback with the event."""
        await self._callback(event)


class EventBus:
    """Assigns per-run sequence numbers, redacts, and fans out events.

    ``seq`` is monotonic per ``run_id`` starting at 1; the counter map is guarded
    by an :class:`asyncio.Lock`, so concurrent emits on one bus can never produce
    a duplicate or a gap. Sinks receive events concurrently; ``seq`` remains the
    ordering source of truth (spec §8 indexes ``agent_event(run_id, seq)``).
    """

    def __init__(self, *sinks: EventSink) -> None:
        if not sinks:
            raise ValueError("EventBus requires at least one sink")
        self._sinks: tuple[EventSink, ...] = tuple(sinks)
        self._seq: dict[UUID, int] = {}
        self._lock = asyncio.Lock()

    @property
    def sinks(self) -> tuple[EventSink, ...]:
        """The registered sinks, in fan-out order."""
        return self._sinks

    async def emit(
        self, run_id: UUID, type: EventType, payload: Mapping[str, object]
    ) -> AgentEvent:
        """Redact ``payload`` and emit a new event with the next per-run ``seq``.

        Returns the event that was broadcast, so callers can persist it or
        inspect the redacted payload directly.
        """
        event = AgentEvent(
            id=uuid4(),
            run_id=run_id,
            seq=await self._next_seq(run_id),
            type=type,
            payload=redact(payload, event_type=type),
            created_at=datetime.now(UTC),
        )
        await self._fan_out(event)
        return event

    async def emit_model(self, run_id: UUID, event: AgentEvent) -> AgentEvent:
        """Re-broadcast an already-built event (e.g. a row reloaded for replay).

        The ``seq`` is preserved — replay reconstructs the persisted order, it
        does not mint a new one. The payload is re-redacted as defence in depth,
        and ``run_id`` is checked against ``event.run_id`` so a replay can never
        cross run boundaries.
        """
        if event.run_id != run_id:
            raise ValueError(
                f"emit_model run_id {run_id} does not match event.run_id {event.run_id}"
            )
        safe = event.model_copy(
            update={"payload": redact(event.payload, event_type=event.type)}
        )
        await self._fan_out(safe)
        return safe

    async def _next_seq(self, run_id: UUID) -> int:
        async with self._lock:
            seq = self._seq.get(run_id, 0) + 1
            self._seq[run_id] = seq
            return seq

    async def _fan_out(self, event: AgentEvent) -> None:
        for sink in self._sinks:
            await sink.emit(event)
