"""Single-pass review harness (spec 10.6).

One agent runs per PR target: it builds the layered prompt, calls the model
once, parses strict findings JSON, grounds them against the changed files,
and retries exactly once with a repair instruction if parsing fails.

Model and parse failures never raise; the harness always returns a
:class:`ReviewResult` so the pipeline can complete and post what it has.
"""

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from slopolis_core.config.repo_config import RepoConfig
from slopolis_core.context import PrContext
from slopolis_core.findings import (
    Finding,
    FindingsParseError,
    drop_ungrounded,
    parse_findings,
)
from slopolis_core.llm.client import LlmClient, LlmError
from slopolis_core.llm.models import ChatMessage
from slopolis_core.prompts.review import compose_review_prompt

__all__ = [
    "RepoContextProvider",
    "ReviewHarness",
    "ReviewResult",
]

_REPAIR_NOTE = (
    "PARSING REPAIR: Your previous response was not valid findings JSON and could "
    'not be used. Respond again with ONLY a JSON object of the exact shape '
    '{"findings":[{"path","line","severity","category","message","suggestion",'
    '"confidence"}]} and nothing else.'
)
_UNPARSEABLE_NOTE = "model output could not be parsed; no findings posted"
_TRUNCATED_NOTE = (
    "PR context exceeded limits; reviewed the diff only and the review is partial."
)


class ReviewResult(BaseModel):
    """Outcome of one review pass, including usage and limitation notes."""

    model_config = ConfigDict(extra="forbid")

    findings: list[Finding]
    model: str
    provider: str
    tokens: int
    cost_usd: float
    truncated: bool = False
    notes: list[str] = []
    raw: str | None = None


@runtime_checkable
class RepoContextProvider(Protocol):
    """Supplies bounded PR context (implemented later, e.g. GitHub API)."""

    async def get_pr_context(self, repo_full_name: str, number: int) -> PrContext: ...


def _context_truncated(context: PrContext) -> bool:
    """Return True when the context lacks a full diff to review."""
    return not context.diff.strip()


def _compose_messages(prompt: str, *, repair: bool) -> list[ChatMessage]:
    """Build the message list, appending the repair note on the retry."""
    messages = [ChatMessage(role="user", content=prompt)]
    if repair:
        messages.append(ChatMessage(role="user", content=_REPAIR_NOTE))
    return messages


class _Usage:
    """Accumulated tokens/cost across one or more model calls."""

    def __init__(self) -> None:
        self.tokens = 0
        self.cost = 0.0

    def add(self, tokens: int, cost: float) -> None:
        self.tokens += tokens
        self.cost += cost


class ReviewHarness:
    """Run one single-pass review for one pull request."""

    def __init__(self, llm: LlmClient, context_provider: RepoContextProvider) -> None:
        self._llm = llm
        self._context_provider = context_provider

    async def review(
        self,
        *,
        repo_full_name: str,
        number: int,
        session_prompt: str | None,
        repo_config: RepoConfig,
        model: str,
        provider: str,
    ) -> ReviewResult:
        """Produce findings for one PR, never raising on model failures."""
        context = await self._context_provider.get_pr_context(repo_full_name, number)
        prompt = compose_review_prompt(repo_config, context, session_prompt)
        truncated = _context_truncated(context)

        usage = _Usage()
        first_text = await self._call(model, prompt, usage=usage, repair=False)
        findings = None if first_text is None else self._parse(first_text)
        text = first_text

        if findings is None:
            repair_text = await self._call(model, prompt, usage=usage, repair=True)
            findings = None if repair_text is None else self._parse(repair_text)
            if repair_text is not None:
                text = repair_text

        notes: list[str] = []
        if truncated:
            notes.append(_TRUNCATED_NOTE)
        if findings is None:
            notes.append(_UNPARSEABLE_NOTE)
            return ReviewResult(
                findings=[],
                model=model,
                provider=provider,
                tokens=usage.tokens,
                cost_usd=usage.cost,
                truncated=truncated,
                notes=notes,
                raw=text,
            )

        grounded = drop_ungrounded(findings, set(context.changed_files))
        return ReviewResult(
            findings=grounded,
            model=model,
            provider=provider,
            tokens=usage.tokens,
            cost_usd=usage.cost,
            truncated=truncated,
            notes=notes,
            raw=text,
        )

    async def _call(
        self, model: str, prompt: str, *, usage: _Usage, repair: bool
    ) -> str | None:
        """Call the model once; accumulate usage; return text or None on error."""
        try:
            result = await self._llm.complete(
                _compose_messages(prompt, repair=repair), model=model
            )
        except LlmError:
            return None
        usage.add(result.total_tokens, result.cost_usd)
        return result.text

    @staticmethod
    def _parse(text: str) -> list[Finding] | None:
        """Parse findings JSON, returning None on parse failure."""
        try:
            return parse_findings(text)
        except FindingsParseError:
            return None
