"""Tests for the single-pass review harness (spec 10.6).

The LLM and context provider are in-memory fakes; no network is used.
"""

from slopolis_core.config.repo_config import RepoConfig
from slopolis_core.context import PrContext
from slopolis_core.domain import Severity
from slopolis_core.llm.client import LlmError
from slopolis_core.llm.models import ChatMessage, CompletionResult
from slopolis_core.review.harness import ReviewHarness, ReviewResult

_VALID_JSON = (
    '{"findings": [{"path": "src/a.py", "line": 3, "severity": "error", '
    '"category": "correctness", "message": "boom", "suggestion": "fix", '
    '"confidence": 0.9}]}'
)
_UNGROUNDED_JSON = (
    '{"findings": [{"path": "src/other.py", "line": 1, "severity": "warning", '
    '"category": "style", "message": "nope", "suggestion": null, '
    '"confidence": 0.5}]}'
)


class FakeLlm:
    """Returns queued responses in order; records the calls it received."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
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
        text = self._responses.pop(0)
        return CompletionResult(
            text=text,
            model=model,
            provider="litellm",
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
            cost_usd=0.001,
        )


class FakeContext:
    """Returns a fixed PrContext for the requested PR."""

    def __init__(self, context: PrContext) -> None:
        self._context = context

    async def get_pr_context(self, repo_full_name: str, number: int) -> PrContext:
        assert repo_full_name == self._context.repo_full_name
        assert number == self._context.number
        return self._context


def _context(*, diff: str = "diff --git a/src/a.py b/src/a.py\n+boom") -> PrContext:
    return PrContext(
        repo_full_name="acme/api",
        number=42,
        title="Fix boom",
        body="body",
        changed_files=["src/a.py"],
        diff=diff,
    )


async def _run(harness: ReviewHarness) -> ReviewResult:
    return await harness.review(
        repo_full_name="acme/api",
        number=42,
        session_prompt=None,
        repo_config=RepoConfig(),
        model="gpt-4o",
        provider="litellm",
    )


async def test_valid_json_returns_grounded_findings() -> None:
    """Given valid grounded JSON, findings pass through with usage."""
    harness = ReviewHarness(FakeLlm([_VALID_JSON]), FakeContext(_context()))

    result = await _run(harness)

    assert len(result.findings) == 1
    assert result.findings[0].severity is Severity.ERROR
    assert result.tokens == 15
    assert result.cost_usd == 0.001
    assert result.truncated is False


async def test_ungrounded_finding_is_dropped() -> None:
    """Given a finding on an unchanged path, it is dropped."""
    harness = ReviewHarness(FakeLlm([_UNGROUNDED_JSON]), FakeContext(_context()))

    result = await _run(harness)

    assert result.findings == []


async def test_malformed_then_valid_retries_once() -> None:
    """Given malformed then valid output, the harness retries once and succeeds."""
    llm = FakeLlm(["not json", _VALID_JSON])
    harness = ReviewHarness(llm, FakeContext(_context()))

    result = await _run(harness)

    assert len(result.findings) == 1
    assert len(llm.calls) == 2
    assert any("PARSING REPAIR" in m.content for m in llm.calls[1])


async def test_malformed_twice_returns_empty_with_note() -> None:
    """Given malformed output twice, the harness notes it and returns no findings."""
    llm = FakeLlm(["nope", "still nope"])
    harness = ReviewHarness(llm, FakeContext(_context()))

    result = await _run(harness)

    assert result.findings == []
    assert len(llm.calls) == 2
    assert any("could not be parsed" in note for note in result.notes)


async def test_truncated_context_sets_flag_and_note() -> None:
    """Given an empty diff, truncated is set with a limitation note."""
    harness = ReviewHarness(FakeLlm([_VALID_JSON]), FakeContext(_context(diff="")))

    result = await _run(harness)

    assert result.truncated is True
    assert any("diff only" in note for note in result.notes)
    assert len(result.findings) == 1


async def test_llm_error_returns_empty_with_note_not_raise() -> None:
    """Given the model errors twice, the harness never raises."""
    class FailingLlm:
        async def complete(
            self,
            messages: list[ChatMessage],
            *,
            model: str,
            max_tokens: int | None = None,
            temperature: float = 0.0,
        ) -> CompletionResult:
            raise LlmError("down")

    harness = ReviewHarness(FailingLlm(), FakeContext(_context()))

    result = await _run(harness)

    assert result.findings == []
    assert any("could not be parsed" in note for note in result.notes)
