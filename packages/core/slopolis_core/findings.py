"""Strict Finding model and parser for review-harness output (spec 10.6).

The model output is **strict structured JSON** validated before use. Findings
are parsed at the boundary exactly once via :func:`parse_findings`; interior
code receives typed `Finding` values and never re-validates.
"""

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from slopolis_core.domain import Severity

__all__ = [
    "Finding",
    "FindingsEnvelope",
    "FindingsParseError",
    "drop_ungrounded",
    "parse_findings",
]


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
    confidence: float = Field(ge=0.0, le=1.0)

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
