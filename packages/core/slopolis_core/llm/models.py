"""Strict chat and completion models for the model gateway (spec 10.2).

The gateway is OpenAI-compatible, so :class:`ChatMessage` mirrors the wire
``{role, content}`` shape and :class:`CompletionResult` is the normalized
answer every provider adapter must produce. Parsing happens at the HTTP
boundary in :mod:`slopolis_core.llm.client`; interior code receives these
typed values and never re-validates.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict

__all__ = ["ChatMessage", "CompletionResult", "Role"]

Role = Literal["system", "user", "assistant"]


class ChatMessage(BaseModel):
    """One message in a chat-completion request."""

    model_config = ConfigDict(extra="forbid", strict=True)

    role: Role
    content: str


class CompletionResult(BaseModel):
    """Normalized result of one chat completion, with usage and cost."""

    model_config = ConfigDict(extra="forbid", strict=True)

    text: str
    model: str
    provider: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_usd: float
