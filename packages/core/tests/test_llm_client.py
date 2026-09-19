"""Tests for the OpenAI-compatible model gateway client (spec 10.2).

All network is faked with ``respx``; no real calls are made.
"""

import httpx
import pytest
import respx

from slopolis_core.llm.client import (
    LiteLlmClient,
    LlmAuthError,
    LlmError,
    LlmTimeoutError,
)
from slopolis_core.llm.models import ChatMessage
from slopolis_core.llm.pricing import DEFAULT_PRICE, compute_cost

_BASE = "https://gateway.test/v1"
_URL = f"{_BASE}/chat/completions"
_MESSAGES = [ChatMessage(role="user", content="review this")]


def _ok_body(content: str = '{"findings":[]}', **usage: int) -> dict[str, object]:
    return {
        "choices": [{"message": {"role": "assistant", "content": content}}],
        "usage": {
            "prompt_tokens": usage.get("prompt_tokens", 10),
            "completion_tokens": usage.get("completion_tokens", 5),
            "total_tokens": usage.get("total_tokens", 15),
        },
    }


@respx.mock
async def test_complete_parses_text_and_tokens() -> None:
    """Given a well-formed response, text and token counts are normalized."""
    respx.post(_URL).mock(return_value=httpx.Response(200, json=_ok_body("hello")))

    client = LiteLlmClient(_BASE, "sk-test")
    result = await client.complete(_MESSAGES, model="gpt-4o-mini")
    await client.aclose()

    assert result.text == "hello"
    assert result.model == "gpt-4o-mini"
    assert result.provider == "litellm"
    assert result.prompt_tokens == 10
    assert result.completion_tokens == 5
    assert result.total_tokens == 15


@respx.mock
async def test_complete_prefers_reported_hidden_cost() -> None:
    """Given ``_hidden_params.response_cost``, that cost wins over the table."""
    body = _ok_body()
    body["_hidden_params"] = {"response_cost": 0.0042}
    respx.post(_URL).mock(return_value=httpx.Response(200, json=body))

    client = LiteLlmClient(_BASE, "sk-test")
    result = await client.complete(_MESSAGES, model="gpt-4o-mini")
    await client.aclose()

    assert result.cost_usd == pytest.approx(0.0042)


@respx.mock
async def test_complete_prefers_reported_cost_header() -> None:
    """Given the cost response header, it wins over the static table."""
    respx.post(_URL).mock(
        return_value=httpx.Response(
            200, json=_ok_body(), headers={"x-litellm-response-cost": "0.0099"}
        )
    )

    client = LiteLlmClient(_BASE, "sk-test")
    result = await client.complete(_MESSAGES, model="gpt-4o-mini")
    await client.aclose()

    assert result.cost_usd == pytest.approx(0.0099)


@respx.mock
async def test_complete_falls_back_to_computed_cost() -> None:
    """Given no reported cost, the static table computes the cost."""
    respx.post(_URL).mock(return_value=httpx.Response(200, json=_ok_body()))

    client = LiteLlmClient(_BASE, "sk-test")
    result = await client.complete(_MESSAGES, model="gpt-4o-mini")
    await client.aclose()

    assert result.cost_usd == pytest.approx(compute_cost("gpt-4o-mini", 10, 5))


def test_compute_cost_unknown_model_uses_default() -> None:
    """Given an unknown model, the default price applies."""
    expected = (2000 / 1000.0) * DEFAULT_PRICE[0] + (1000 / 1000.0) * DEFAULT_PRICE[1]

    assert compute_cost("mystery-model", 2000, 1000) == pytest.approx(expected)


def test_compute_cost_matches_known_model() -> None:
    """Given a known model, its per-1k rates produce the cost."""
    # gpt-4o: prompt 0.0025, completion 0.010 per 1k tokens.
    assert compute_cost("gpt-4o", 1000, 1000) == pytest.approx(0.0125)


@respx.mock
async def test_complete_401_raises_auth_error() -> None:
    """Given HTTP 401, the client raises LlmAuthError."""
    respx.post(_URL).mock(return_value=httpx.Response(401, json={"error": "nope"}))

    client = LiteLlmClient(_BASE, "sk-test")
    with pytest.raises(LlmAuthError):
        await client.complete(_MESSAGES, model="gpt-4o-mini")
    await client.aclose()


@respx.mock
async def test_complete_500_raises_llm_error() -> None:
    """Given a server error, the client raises LlmError."""
    respx.post(_URL).mock(return_value=httpx.Response(500, json={"error": "boom"}))

    client = LiteLlmClient(_BASE, "sk-test")
    with pytest.raises(LlmError):
        await client.complete(_MESSAGES, model="gpt-4o-mini")
    await client.aclose()


@respx.mock
async def test_complete_timeout_raises_timeout_error() -> None:
    """Given a timeout, the client raises LlmTimeoutError."""
    respx.post(_URL).mock(side_effect=httpx.ConnectTimeout("timed out"))

    client = LiteLlmClient(_BASE, "sk-test", timeout_s=0.01)
    with pytest.raises(LlmTimeoutError):
        await client.complete(_MESSAGES, model="gpt-4o-mini")
    await client.aclose()


@respx.mock
async def test_complete_sends_bearer_and_optional_max_tokens() -> None:
    """Given max_tokens, the request carries it plus Bearer auth."""
    route = respx.post(_URL).mock(return_value=httpx.Response(200, json=_ok_body()))

    client = LiteLlmClient(_BASE, "sk-secret")
    await client.complete(_MESSAGES, model="gpt-4o", max_tokens=1)
    await client.aclose()

    request = route.calls.last.request
    assert request.headers["authorization"] == "Bearer sk-secret"
    assert b'"max_tokens"' in request.content
