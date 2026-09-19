"""Tests for the harness event bus and redaction layer (plan Task 1.3).

The security-critical assertion (spec §10, plan acceptance) is that a seeded
secret never appears in any emitted payload, in any position.
"""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from slopolis_core.harness import (
    AgentEvent,
    CallbackSink,
    CollectingSink,
    EventBus,
    EventSink,
    EventType,
    redact,
)

_RUN_A = uuid4()
_RUN_B = uuid4()

_API_KEY = "sk-" + "a" * 40
_BEARER_TOKEN = "Z" * 32
_BEARER = f"Bearer {_BEARER_TOKEN}"
_GITHUB_PAT = "ghp_" + "b" * 36
_AWS_KEY = "AKIA" + "C" * 16
_PEM = (
    "-----BEGIN RSA PRIVATE KEY-----\n"
    "MIIEowIBAAKCAQEA1234567890abcdefghijklmnop\n"
    "-----END RSA PRIVATE KEY-----"
)
#: Values seeded into payload fields; each is a recognizable credential shape.
_SEEDED_SECRETS = (_API_KEY, _BEARER, _GITHUB_PAT, _AWS_KEY, _PEM)
#: Substrings that must never survive, including the token inside a Bearer header.
_FORBIDDEN = (*_SEEDED_SECRETS, _BEARER_TOKEN)

_PLACEHOLDER = "[redacted]"
_TRUNCATION_MARKER = "...[truncated]"
_MAX_STRING_LEN = 2000


async def _noop(_event: AgentEvent) -> None:
    return None


def _event(run_id: UUID, event_type: EventType, payload: dict[str, object]) -> AgentEvent:
    return AgentEvent(
        id=uuid4(),
        run_id=run_id,
        seq=7,
        type=event_type,
        payload=payload,
        created_at=datetime(2026, 9, 16, 12, 0, tzinfo=UTC),
    )


async def test_seeded_secrets_never_reach_any_emitted_payload() -> None:
    sink = CollectingSink()
    bus = EventBus(sink)

    for secret in _SEEDED_SECRETS:
        await bus.emit(
            _RUN_A,
            EventType.STEP,
            {
                "model_id": f"model-{secret}",
                "summary": f"leaked {secret} near the end",
                "raw_output": secret,
                "transcript": [secret],
            },
        )
    await bus.emit(_RUN_A, EventType.FAILED, {"error": f"provider rejected {_BEARER}"})

    assert len(sink) == len(_SEEDED_SECRETS) + 1
    for event in sink.events:
        dumped = json.dumps(event.payload)
        for secret in _FORBIDDEN:
            assert secret not in dumped
        for value in event.payload.values():
            if isinstance(value, str):
                for secret in _FORBIDDEN:
                    assert secret not in value


async def test_redact_masks_every_secret_shape_in_place() -> None:
    safe = redact(
        {
            "summary": f"key={_API_KEY} pat={_GITHUB_PAT} aws={_AWS_KEY} bearer={_BEARER}",
            "error": _PEM,
        },
        event_type=EventType.FAILED,
    )

    summary = safe["summary"]
    expected = (
        f"key={_PLACEHOLDER} pat={_PLACEHOLDER} aws={_PLACEHOLDER} bearer={_PLACEHOLDER}"
    )
    assert isinstance(summary, str)
    assert summary == expected
    assert safe["error"] == _PLACEHOLDER
    assert redact(safe, event_type=EventType.FAILED) == safe


async def test_redact_drops_keys_and_values_outside_the_allow_list() -> None:
    payload: dict[str, object] = {
        "tool": "read_file",
        "args_summary": "src/a.py",
        "secret": "ghp_leak",
        "raw_output": {"findings": []},
    }

    assert redact(payload, event_type=EventType.TOOL_CALL) == {
        "tool": "read_file",
        "args_summary": "src/a.py",
    }
    assert redact({"summary": {"raw": "model transcript"}}, event_type=EventType.STEP) == {}
    assert redact({"error": "boom"}, event_type=EventType.FAILED) == {"error": "boom"}
    assert redact({"error": "boom"}, event_type=EventType.TOOL_CALL) == {}


async def test_redact_accepts_payload_without_an_event_type() -> None:
    safe = redact({"tool": "read_file", "service_token": "sk-" + "d" * 40})

    assert set(safe) == {"tool"}
    assert safe["tool"] == "read_file"


async def test_long_strings_are_truncated_and_marked() -> None:
    sink = CollectingSink()
    bus = EventBus(sink)
    long_value = "x" * 5000

    event = await bus.emit(_RUN_A, EventType.STEP, {"summary": long_value})
    summary = event.payload["summary"]

    assert isinstance(summary, str)
    assert summary.endswith(_TRUNCATION_MARKER)
    assert len(summary) == _MAX_STRING_LEN + len(_TRUNCATION_MARKER)
    assert summary[:_MAX_STRING_LEN] == "x" * _MAX_STRING_LEN


async def test_emit_builds_event_with_uuid_and_tz_aware_timestamp() -> None:
    sink = CollectingSink()
    bus = EventBus(sink)

    event = await bus.emit(_RUN_A, EventType.STARTED, {"role": "orchestrator.pr"})

    assert isinstance(event.id, UUID)
    assert event.type is EventType.STARTED
    assert event.run_id == _RUN_A
    assert event.created_at.tzinfo is not None
    assert event.created_at.utcoffset() == timedelta(0)
    assert sink.events == [event]


async def test_seq_is_monotonic_and_independent_per_run() -> None:
    sink = CollectingSink()
    bus = EventBus(sink)

    run_a = [await bus.emit(_RUN_A, EventType.STEP, {}) for _ in range(3)]
    run_b = [await bus.emit(_RUN_B, EventType.STEP, {}) for _ in range(2)]
    run_a_more = [await bus.emit(_RUN_A, EventType.STEP, {})]

    assert [event.seq for event in run_a] == [1, 2, 3]
    assert [event.seq for event in run_b] == [1, 2]
    assert [event.seq for event in run_a_more] == [4]
    assert all(event.run_id == _RUN_A for event in run_a + run_a_more)
    assert all(event.run_id == _RUN_B for event in run_b)


async def test_concurrent_emits_yield_a_gap_free_unique_sequence() -> None:
    sink = CollectingSink()
    bus = EventBus(sink)

    events = await asyncio.gather(
        *(bus.emit(_RUN_A, EventType.STEP, {"step": index}) for index in range(50))
    )

    assert sorted(event.seq for event in events) == list(range(1, 51))
    assert len({event.id for event in events}) == 50
    assert len(sink) == 50


async def test_fans_out_to_sinks_in_construction_order() -> None:
    order: list[str] = []

    async def first(_event: AgentEvent) -> None:
        order.append("first")

    async def second(_event: AgentEvent) -> None:
        order.append("second")

    bus = EventBus(CallbackSink(first), CallbackSink(second))

    await bus.emit(_RUN_A, EventType.STARTED, {})
    await bus.emit(_RUN_A, EventType.COMPLETED, {})

    assert order == ["first", "second", "first", "second"]


async def test_collecting_sink_contents_match_emissions() -> None:
    sink = CollectingSink()
    bus = EventBus(sink)

    started = await bus.emit(_RUN_A, EventType.STARTED, {"role": "orchestrator.pr"})
    completed = await bus.emit(_RUN_A, EventType.COMPLETED, {"status": "done"})

    assert sink.events == [started, completed]
    assert [event.type for event in sink.events] == [
        EventType.STARTED,
        EventType.COMPLETED,
    ]


async def test_emit_model_preserves_seq_and_re_redacts() -> None:
    sink = CollectingSink()
    bus = EventBus(sink)
    replayed_input = _event(_RUN_A, EventType.FAILED, {"error": f"key {_API_KEY}"})

    replayed = await bus.emit_model(_RUN_A, replayed_input)

    assert sink.events == [replayed]
    assert replayed.seq == 7
    assert replayed.payload["error"] == f"key {_PLACEHOLDER}"


async def test_emit_model_rejects_a_mismatched_run_id() -> None:
    bus = EventBus(CollectingSink())

    with pytest.raises(ValueError, match="does not match"):
        await bus.emit_model(_RUN_B, _event(_RUN_A, EventType.STARTED, {}))


def test_event_bus_requires_at_least_one_sink() -> None:
    with pytest.raises(ValueError, match="at least one sink"):
        EventBus()


def test_built_in_sinks_satisfy_the_event_sink_protocol() -> None:
    assert isinstance(CollectingSink(), EventSink)
    assert isinstance(CallbackSink(_noop), EventSink)
