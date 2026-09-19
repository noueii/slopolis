"""OpenAI-compatible model gateway client (spec 10.2).

:class:`LiteLlmClient` speaks the OpenAI ``/chat/completions`` shape, so it
works against LiteLLM and any OpenAI-compatible endpoint. It parses the
response into a typed :class:`CompletionResult` at the HTTP boundary exactly
once and translates transport failures into typed errors. The API key is
never logged.
"""

from typing import Any, Protocol, cast, runtime_checkable

import httpx
from pydantic import ValidationError

from slopolis_core.llm.models import ChatMessage, CompletionResult
from slopolis_core.llm.pricing import compute_cost
from slopolis_core.settings import get_settings

__all__ = [
    "LiteLlmClient",
    "LlmAuthError",
    "LlmClient",
    "LlmError",
    "LlmTimeoutError",
]

_PROVIDER = "litellm"
_COST_HEADER = "x-litellm-response-cost"


class LlmError(RuntimeError):
    """Base class for model-gateway failures."""


class LlmAuthError(LlmError):
    """The gateway rejected the credentials (HTTP 401/403)."""


class LlmTimeoutError(LlmError):
    """The gateway did not respond within the configured timeout."""


@runtime_checkable
class LlmClient(Protocol):
    """Structural type for a chat-completion client.

    Preflight live checks and the review harness depend only on this protocol,
    so tests and alternative providers can substitute a fake.
    """

    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        model: str,
        max_tokens: int | None = None,
        temperature: float = 0.0,
    ) -> CompletionResult: ...


def _first_choice_text(payload: dict[str, Any]) -> str:
    """Return ``choices[0].message.content`` or raise a typed error."""
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise LlmError("Gateway response contained no choices")
    first = cast("dict[str, Any]", choices[0])
    message = first.get("message")
    if not isinstance(message, dict):
        raise LlmError("Gateway response choice had no message")
    content = cast("dict[str, Any]", message).get("content")
    if not isinstance(content, str):
        raise LlmError("Gateway response message had no text content")
    return content


def _usage_tokens(payload: dict[str, Any]) -> tuple[int, int, int]:
    """Return ``(prompt, completion, total)`` tokens, defaulting missing to 0."""
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return 0, 0, 0
    usage_map = cast("dict[str, Any]", usage)

    def _as_int(key: str) -> int:
        value = usage_map.get(key)
        return value if isinstance(value, int) else 0

    prompt = _as_int("prompt_tokens")
    completion = _as_int("completion_tokens")
    total = _as_int("total_tokens") or (prompt + completion)
    return prompt, completion, total


def _reported_cost(
    payload: dict[str, Any], headers: httpx.Headers
) -> float | None:
    """Return the gateway-reported cost, preferring ``_hidden_params``."""
    hidden = payload.get("_hidden_params")
    if isinstance(hidden, dict):
        value = cast("dict[str, Any]", hidden).get("response_cost")
        if isinstance(value, (int, float)):
            return float(value)

    header = headers.get(_COST_HEADER)
    if header is not None:
        try:
            return float(header)
        except ValueError:
            return None
    return None


class LiteLlmClient:
    """Chat client for LiteLLM and any OpenAI-compatible gateway."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        timeout_s: float = 60.0,
        provider: str = _PROVIDER,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._provider = provider
        self._client = httpx.AsyncClient(timeout=timeout_s)

    @classmethod
    def from_settings(cls) -> "LiteLlmClient":
        """Build a client from process settings; requires a master key."""
        settings = get_settings()
        api_key = settings.litellm_master_key
        if not api_key:
            raise LlmAuthError("LITELLM_MASTER_KEY is not configured")
        return cls(settings.litellm_base_url, api_key)

    async def aclose(self) -> None:
        """Close the underlying HTTP connection pool."""
        await self._client.aclose()

    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        model: str,
        max_tokens: int | None = None,
        temperature: float = 0.0,
    ) -> CompletionResult:
        """Run one chat completion and normalize the response."""
        body: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": message.role, "content": message.content}
                for message in messages
            ],
            "temperature": temperature,
        }
        if max_tokens is not None:
            body["max_tokens"] = max_tokens

        try:
            response = await self._client.post(
                f"{self._base_url}/chat/completions",
                json=body,
                headers={"Authorization": f"Bearer {self._api_key}"},
            )
        except httpx.TimeoutException as exc:
            raise LlmTimeoutError(
                f"Gateway timed out after {self._client.timeout} seconds"
            ) from exc
        except httpx.HTTPError as exc:
            raise LlmError(f"Gateway request failed: {exc}") from exc

        if response.status_code in (401, 403):
            raise LlmAuthError(
                f"Gateway rejected credentials (HTTP {response.status_code})"
            )
        if response.status_code >= 400:
            raise LlmError(f"Gateway returned HTTP {response.status_code}")

        try:
            payload: object = response.json()
        except ValueError as exc:
            raise LlmError("Gateway returned a non-JSON body") from exc
        if not isinstance(payload, dict):
            raise LlmError("Gateway returned a non-object JSON body")

        payload_map = cast("dict[str, Any]", payload)
        text = _first_choice_text(payload_map)
        prompt_tokens, completion_tokens, total_tokens = _usage_tokens(payload_map)
        reported = _reported_cost(payload_map, response.headers)
        cost = (
            reported
            if reported is not None
            else compute_cost(model, prompt_tokens, completion_tokens)
        )

        try:
            return CompletionResult(
                text=text,
                model=model,
                provider=self._provider,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                cost_usd=cost,
            )
        except ValidationError as exc:
            raise LlmError(f"Gateway response failed validation: {exc}") from exc
