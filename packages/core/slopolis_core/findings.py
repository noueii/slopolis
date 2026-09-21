"""Strict Finding model and parser for review-harness output (spec 10.6).

The model output is **strict structured JSON** validated before use. Findings
are parsed at the boundary exactly once via :func:`parse_findings`; interior
code receives typed `Finding` values and never re-validates.
"""

import json
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from slopolis_core.domain import Severity

__all__ = [
    "Finding",
    "FindingsEnvelope",
    "FindingsParseError",
    "drop_ungrounded",
    "is_code_shaped",
    "parse_findings",
]

#: Consecutive plain words that make a line read as a sentence.
_PROSE_WORD_RUN = 3

#: A line ending in one of these, holding at least two words, reads as a sentence.
_SENTENCE_ENDINGS = (".", "!", "?")

#: Punctuation that wraps a word in prose — `` `code` ``, ``(aside)``, quotes —
#: and so does not stop it being a word. Commas, semicolons, colons, slashes,
#: underscores, and hyphens are deliberately absent: they separate code tokens
#: (`return nil, err`) and must break a word run.
_WORD_EDGES = "`\"'()[]{}*_.!?"

_WORD = re.compile(r"[A-Za-z][A-Za-z']*")

#: ``(marker, needs_space_before)`` for text that starts a trailing comment.
#: ``-- `` requires the space so a decrement (`count--`) is not read as one.
_COMMENT_MARKERS: tuple[tuple[str, bool], ...] = (
    ("#", False),
    ("//", False),
    ("-- ", True),
)


class FindingsParseError(ValueError):
    """Raised when model output is not a valid findings envelope."""


class Finding(BaseModel):
    """A single review finding on one file (spec overview §9)."""

    model_config = ConfigDict(extra="forbid", strict=True)

    path: str
    line: int | None = None
    severity: Severity
    category: str
    message: str
    suggestion: str | None = None
    """The literal replacement for the cited line(s), or ``None`` for no fix.

    Not a description of the fix: GitHub renders this in a ``suggestion`` block
    and *Commit suggestion* substitutes it for the line, so it must be the exact
    text that belongs in the file — in the file's own language, only the lines
    being replaced, with no prose, explanation, or fence. Advice that cannot be
    written as replacement code belongs in :attr:`message`.
    """
    confidence: float = Field(ge=0.0, le=1.0)

    @field_validator("suggestion", mode="before")
    @classmethod
    def _blank_suggestion_is_none(cls, value: object) -> object:
        """Fold a blank suggestion onto ``None``, the documented no-fix value.

        Whitespace states no fix, and keeping it would render an empty block.
        Anything else is left as written: prose in this field is a rendering
        problem, not a schema error — :func:`is_code_shaped` decides whether it
        can be applied or only read, and rejecting the finding here would throw
        away usable advice over a formatting slip.
        """
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("severity", mode="before")
    @classmethod
    def _coerce_severity(cls, value: object) -> object:
        """Map the wire string onto the enum before strict validation.

        JSON carries severities as strings; strict mode requires enum
        instances. Coercing here keeps ``extra='forbid'`` and rejects any
        value outside the canonical severity set.
        """
        if isinstance(value, str):
            try:
                return Severity(value)
            except ValueError as exc:
                raise ValueError(f"invalid severity {value!r}") from exc
        return value


class FindingsEnvelope(BaseModel):
    """Top-level JSON object the model must emit: ``{"findings": [...]}``."""

    model_config = ConfigDict(extra="forbid", strict=True)

    findings: list[Finding]


def is_code_shaped(suggestion: str) -> bool:
    """Whether ``suggestion`` reads as replacement code rather than prose.

    A suggestion is only safe to render as an applyable ``suggestion`` block
    when it could plausibly be source text, so the test is deliberately
    one-sided: reading prose as code offers a button that replaces a line with
    a sentence, while reading code as prose merely costs a copy-paste. Every
    non-blank line must therefore clear the prose test; an empty or
    whitespace-only suggestion is not code.
    """
    lines = [line for line in suggestion.splitlines() if line.strip()]
    return bool(lines) and not any(_line_reads_as_prose(line) for line in lines)


def _line_reads_as_prose(line: str) -> bool:
    """Whether one line of a suggestion reads as prose.

    A line that is nothing but a comment carries no code to judge, so the
    comment's own text decides: a terse ``# noqa`` is code, while
    ``# Retry once before giving up.`` is prose wearing a comment marker.
    """
    code, comment = _split_comment(line)
    return _reads_as_prose(code if code.strip() else comment)


def _reads_as_prose(text: str) -> bool:
    """Whether ``text`` is a sentence rather than source.

    Two signals, both cheap and conservative: a run of plain words (`Make the
    delete atomic`), or a line written out with sentence punctuation at the
    end. The word run is what separates code from English — punctuation
    attached to a word does not break the run, but anything that separates
    tokens does, which is why `return nil, err` stays code.
    """
    longest = current = words = 0
    for token in text.split():
        if _WORD.fullmatch(token.strip(_WORD_EDGES)):
            current += 1
            words += 1
            longest = max(longest, current)
        else:
            current = 0
    if longest >= _PROSE_WORD_RUN:
        return True
    return words >= 2 and text.rstrip().endswith(_SENTENCE_ENDINGS)


def _split_comment(line: str) -> tuple[str, str]:
    """Split ``line`` into its code part and the trailing comment, if any."""
    start = _comment_start(line)
    if start is None:
        return line, ""
    index, marker = start
    return line[:index], line[index + len(marker) :]


def _comment_start(line: str) -> tuple[int, str] | None:
    """Index and marker of the comment opening on ``line``, if one does."""
    found: tuple[int, str] | None = None
    for marker, needs_space_before in _COMMENT_MARKERS:
        index = line.find(marker)
        while index != -1:
            before = line[index - 1] if index else ""
            # `://` is a URL scheme, and `count--` is a decrement, not a comment.
            if before != ":" and not (needs_space_before and before and not before.isspace()):
                if found is None or index < found[0]:
                    found = (index, marker)
                break
            index = line.find(marker, index + len(marker))
    return found


def _strip_code_fence(raw: str) -> str:
    """Drop a leading/trailing markdown code fence if one is present."""
    text = raw.strip()
    if not text.startswith("```"):
        return text
    # Remove the opening fence line (```json or ```).
    without_open = text.split("\n", 1)[1] if "\n" in text else ""
    closing = without_open.rfind("```")
    if closing != -1:
        without_open = without_open[:closing]
    return without_open.strip()


def _extract_json_object(raw: str) -> str:
    """Return the outermost JSON object substring, or raise if none exists."""
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise FindingsParseError(
            "Model output contained no JSON object; expected "
            '{"findings": [...]}'
        )
    return raw[start : end + 1]


def parse_findings(raw: str) -> list[Finding]:
    """Parse strict findings JSON from raw model output.

    Handles fenced JSON, surrounding prose, and validates the envelope shape.
    Raises :class:`FindingsParseError` (never a bare ``KeyError`` or
    ``json.JSONDecodeError``) when the output is malformed.
    """
    if not raw or not raw.strip():
        raise FindingsParseError("Model output was empty; expected JSON findings")

    candidate_text = _strip_code_fence(raw)
    json_text = _extract_json_object(candidate_text)

    try:
        payload: Any = json.loads(json_text)
    except json.JSONDecodeError as exc:
        raise FindingsParseError(f"Model output was not valid JSON: {exc.msg}") from exc

    if not isinstance(payload, dict):
        raise FindingsParseError(
            f"Expected a JSON object, got {type(payload).__name__}"
        )

    try:
        envelope = FindingsEnvelope.model_validate(payload)
    except ValidationError as exc:
        raise FindingsParseError(f"Findings failed schema validation: {exc}") from exc

    return envelope.findings


def drop_ungrounded(findings: list[Finding], changed_paths: set[str]) -> list[Finding]:
    """Return only findings whose ``path`` is in ``changed_paths``.

    Grounding rule: a finding must point at a file actually touched by the PR.
    Order is preserved.
    """
    return [finding for finding in findings if finding.path in changed_paths]
