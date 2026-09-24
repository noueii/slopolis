"""Record every real model call as a turn (spec v2 11.3).

The turn is the unit a prompt can be judged on: the messages as they went to the
gateway, the raw completion that came back, and what it cost. It is recorded at
this seam — :meth:`LlmClient.complete`, the one place a call is actually made —
rather than at the tool-loop, because the loop is not always the caller: a
reviewer run hands its turn to the review harness, which composes the prompt the
model really receives and ignores the transcript the loop would have sent.

``provider`` is carried for completeness: a turn names the gateway that served it
as well as the model, since one deployment can hold several.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from slopolis_core.harness.events import MAX_TURN_CHARS
from slopolis_core.llm.client import LlmClient
from slopolis_core.llm.models import ChatMessage, CompletionResult

__all__ = ["RecordingLlmClient", "TurnRecord", "TurnSink", "turn_payload"]


@dataclass(frozen=True, slots=True)
class TurnRecord:
    """One completed model call, as the caller of :meth:`complete` saw it."""

    model_id: str
    provider: str
    messages: list[ChatMessage]
    response: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_usd: float


#: What a recorder does with a finished turn.
type TurnSink = Callable[[TurnRecord], Awaitable[None]]


class RecordingLlmClient:
    """An :class:`LlmClient` that reports every call it makes to a sink.

    The result is returned untouched: recording is observation, and a caller that
    ignores the sink sees exactly the client it wrapped. A call that raises is
    not recorded — there is no turn to record — and the error propagates as it
    always did.
    """

    def __init__(self, client: LlmClient, sink: TurnSink) -> None:
        self._client = client
        self._sink = sink

    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        model: str,
        max_tokens: int | None = None,
        temperature: float = 0.0,
    ) -> CompletionResult:
        """Run one chat completion, then hand the turn to the sink."""
        result = await self._client.complete(
            messages, model=model, max_tokens=max_tokens, temperature=temperature
        )
        await self._sink(
            TurnRecord(
                model_id=result.model,
                provider=result.provider,
                messages=list(messages),
                response=result.text,
                prompt_tokens=result.prompt_tokens,
                completion_tokens=result.completion_tokens,
                total_tokens=result.total_tokens,
                cost_usd=result.cost_usd,
            )
        )
        return result


def turn_payload(turn: TurnRecord) -> dict[str, object]:
    """Build the allow-listed payload for one ``agent.turn`` event.

    ``truncated`` is decided here, where the untruncated length is still known:
    the redaction layer clamps a turn's strings at :data:`MAX_TURN_CHARS`, and the
    event has to say so rather than look like the prompt ended there.
    """
    messages = [
        {"role": message.role, "content": message.content} for message in turn.messages
    ]
    chars = sum(len(message.content) for message in turn.messages) + len(turn.response)
    truncated = len(turn.response) > MAX_TURN_CHARS or any(
        len(message.content) > MAX_TURN_CHARS for message in turn.messages
    )
    return {
        "model_id": turn.model_id,
        "messages": messages,
        "response": turn.response,
        "prompt_tokens": turn.prompt_tokens,
        "completion_tokens": turn.completion_tokens,
        "total_tokens": turn.total_tokens,
        "cost_usd": turn.cost_usd,
        "chars": chars,
        "truncated": truncated,
    }
