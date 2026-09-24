"""Tests for turn recording and the redaction rules it depends on (spec v2 11.3).

A turn is only worth reading if it is faithful: the messages that were sent and
the text that came back. These tests hold that line, and hold the cap that keeps
one event from growing without limit — with no database and no gateway.
"""

from __future__ import annotations

import pytest

from slopolis_core.harness import EventType, redact
from slopolis_core.harness.events import MAX_TURN_CHARS
from slopolis_core.llm import (
    ChatMessage,
    CompletionResult,
    RecordingLlmClient,
    TurnRecord,
    turn_payload,
)

_MESSAGES = [
    ChatMessage(role="system", content="You review pull requests."),
    ChatMessage(role="user", content="Review this diff: +x = 1"),
]


class FakeClient:
    """A chat client double: answers with a canned completion, or raises."""

    def __init__(
        self, *, text: str = '{"findings": []}', error: Exception | None = None
    ) -> None:
        self._text = text
        self._error = error
        self.calls: list[list[ChatMessage]] = []

    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        model: str,
        max_tokens: int | None = None,
        temperature: float = 0.0,
    ) -> CompletionResult:
        self.calls.append(list(messages))
        if self._error is not None:
            raise self._error
        return CompletionResult(
            text=self._text,
            model=model,
            provider="litellm",
            prompt_tokens=100,
            completion_tokens=20,
            total_tokens=120,
            cost_usd=0.002,
        )


class Sink:
    """Collects the turns a recording client reports."""

    def __init__(self) -> None:
        self.turns: list[TurnRecord] = []

    async def __call__(self, turn: TurnRecord) -> None:
        self.turns.append(turn)


def _turn(**overrides: object) -> TurnRecord:
    """A complete turn record with per-test overrides."""
    fields: dict[str, object] = {
        "model_id": "model-review",
        "provider": "litellm",
        "messages": _MESSAGES,
        "response": '{"findings": []}',
        "prompt_tokens": 100,
        "completion_tokens": 20,
        "total_tokens": 120,
        "cost_usd": 0.002,
    }
    fields.update(overrides)
    return TurnRecord(**fields)  # type: ignore[arg-type]


async def test_a_recorded_turn_is_the_call_that_was_made() -> None:
    client = FakeClient()
    sink = Sink()
    recording = RecordingLlmClient(client, sink)

    result = await recording.complete(_MESSAGES, model="model-review")

    assert client.calls == [_MESSAGES]
    assert result.text == '{"findings": []}'
    assert len(sink.turns) == 1
    turn = sink.turns[0]
    assert turn.messages == _MESSAGES
    assert turn.model_id == "model-review"
    assert turn.provider == "litellm"
    assert turn.response == '{"findings": []}'
    assert (turn.prompt_tokens, turn.completion_tokens, turn.total_tokens) == (100, 20, 120)
    assert turn.cost_usd == pytest.approx(0.002)


async def test_a_failed_call_is_not_a_turn() -> None:
    client = FakeClient(error=RuntimeError("gateway down"))
    sink = Sink()

    with pytest.raises(RuntimeError, match="gateway down"):
        await RecordingLlmClient(client, sink).complete(_MESSAGES, model="model-review")

    assert sink.turns == []


def test_the_payload_carries_the_transcript_and_its_cost() -> None:
    payload = turn_payload(_turn())

    assert payload["messages"] == [
        {"role": "system", "content": "You review pull requests."},
        {"role": "user", "content": "Review this diff: +x = 1"},
    ]
    assert payload["response"] == '{"findings": []}'
    assert payload["chars"] == len("You review pull requests.") + len(
        "Review this diff: +x = 1"
    ) + len('{"findings": []}')
    assert payload["truncated"] is False


def test_the_payload_admits_a_clipped_transcript() -> None:
    long_prompt = "x" * (MAX_TURN_CHARS + 1)

    payload = turn_payload(_turn(messages=[ChatMessage(role="user", content=long_prompt)]))

    assert payload["truncated"] is True
    redacted = redact(payload, event_type=EventType.TURN)
    sent = redacted["messages"]
    assert isinstance(sent, list)
    content = sent[0]["content"]  # type: ignore[index]
    assert isinstance(content, str)
    assert len(content) == MAX_TURN_CHARS + len("...[truncated]")
    assert content.endswith("...[truncated]")


def test_a_turn_keeps_its_transcript_through_redaction() -> None:
    redacted = redact(turn_payload(_turn()), event_type=EventType.TURN)

    assert redacted["messages"] == [
        {"role": "system", "content": "You review pull requests."},
        {"role": "user", "content": "Review this diff: +x = 1"},
    ]
    assert redacted["response"] == '{"findings": []}'
    assert redacted["total_tokens"] == 120


def test_a_credential_inside_a_prompt_is_masked() -> None:
    secret = "ghp_" + "a" * 36

    redacted = redact(
        turn_payload(
            _turn(messages=[ChatMessage(role="user", content=f"key is {secret}")])
        ),
        event_type=EventType.TURN,
    )

    sent = redacted["messages"]
    assert isinstance(sent, list)
    assert sent[0]["content"] == "key is [redacted]"  # type: ignore[index]


def test_a_malformed_transcript_is_dropped_whole() -> None:
    payload = turn_payload(_turn())
    payload["messages"] = [{"role": "user", "content": "fine"}, {"role": "user"}]

    redacted = redact(payload, event_type=EventType.TURN)

    assert "messages" not in redacted
    assert redacted["response"] == '{"findings": []}'


def test_only_a_turn_may_carry_a_transcript() -> None:
    """Every other type keeps its digest rule: a list is dropped, text is capped."""
    payload = {
        "role": "assistant",
        "summary": "y" * 3000,
        "messages": [{"role": "user", "content": "not an event field here"}],
    }

    redacted = redact(payload, event_type=EventType.MESSAGE)

    assert "messages" not in redacted
    summary = redacted["summary"]
    assert isinstance(summary, str)
    assert len(summary) == 2000 + len("...[truncated]")


def test_redacting_twice_changes_nothing() -> None:
    """Replay re-redacts a persisted payload, so the rules must be idempotent."""
    payload = turn_payload(
        _turn(
            messages=[ChatMessage(role="user", content="z" * (MAX_TURN_CHARS + 10))],
            response="r" * (MAX_TURN_CHARS + 10),
        )
    )

    once = redact(payload, event_type=EventType.TURN)
    twice = redact(once, event_type=EventType.TURN)

    assert twice == once
