"""Model-gateway client, models, and pricing (spec 10.2)."""

from slopolis_core.llm.client import (
    LiteLlmClient,
    LlmAuthError,
    LlmClient,
    LlmError,
    LlmTimeoutError,
)
from slopolis_core.llm.models import ChatMessage, CompletionResult, Role
from slopolis_core.llm.pricing import DEFAULT_PRICE, PRICE_TABLE, compute_cost

__all__ = [
    "DEFAULT_PRICE",
    "PRICE_TABLE",
    "ChatMessage",
    "CompletionResult",
    "LiteLlmClient",
    "LlmAuthError",
    "LlmClient",
    "LlmError",
    "LlmTimeoutError",
    "Role",
    "compute_cost",
]
