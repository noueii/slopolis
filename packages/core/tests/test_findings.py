"""Tests for strict finding parsing and grounding (spec 10.6)."""

import pytest
from pydantic import ValidationError

from slopolis_core.domain import Severity
from slopolis_core.findings import (
    Finding,
    FindingsParseError,
    drop_ungrounded,
    parse_findings,
)


def test_parse_valid_envelope() -> None:
    """Given a well-formed envelope, parsing returns the findings."""
    raw = (
        '{"findings": [{"path": "src/a.py", "line": 3, "severity": "error", '
        '"category": "correctness", "message": "boom", "suggestion": "fix it", '
        '"confidence": 0.8}]}'
    )

    findings = parse_findings(raw)

    assert len(findings) == 1
    assert findings[0].path == "src/a.py"
    assert findings[0].severity is Severity.ERROR
    assert findings[0].confidence == 0.8


def test_parse_fenced_json() -> None:
    """Given a fenced JSON block, parsing strips the fence and succeeds."""
    raw = '```json\n{"findings": []}\n```'

    findings = parse_findings(raw)

    assert findings == []


def test_parse_finds_object_inside_prose() -> None:
    """Given prose around the JSON, parsing extracts the object."""
    raw = 'Here you go:\n{"findings": []}\nHope that helps!'

    assert parse_findings(raw) == []


def test_parse_garbage_raises_typed_error() -> None:
    """Given non-JSON text, parsing raises FindingsParseError."""
    with pytest.raises(FindingsParseError):
        parse_findings("this is not json at all")


def test_parse_empty_raises_typed_error() -> None:
    """Given empty output, parsing raises FindingsParseError."""
    with pytest.raises(FindingsParseError):
        parse_findings("   ")


def test_parse_wrong_shape_raises_typed_error() -> None:
    """Given a JSON object without a findings list, parsing raises."""
    with pytest.raises(FindingsParseError):
        parse_findings('{"results": []}')


def test_confidence_bounds_enforced() -> None:
    """Given confidence outside [0, 1], model validation rejects it."""
    with pytest.raises(ValidationError):
        Finding(
            path="a.py",
            severity=Severity.INFO,
            category="style",
            message="m",
            confidence=1.5,
        )


def test_extra_fields_forbidden() -> None:
    """Given an unknown field, model validation rejects the finding."""
    with pytest.raises(ValidationError):
        Finding.model_validate(
            {
                "path": "a.py",
                "severity": "info",
                "category": "style",
                "message": "m",
                "confidence": 0.5,
                "extra": "nope",
            }
        )


def test_drop_ungrounded_filters_and_preserves_order() -> None:
    """Given findings, only grounded paths survive in original order."""
    grounded = Finding(
        path="src/a.py",
        severity=Severity.ERROR,
        category="correctness",
        message="a",
        confidence=0.9,
    )
    ungrounded = Finding(
        path="src/other.py",
        severity=Severity.WARNING,
        category="style",
        message="b",
        confidence=0.5,
    )
    second_grounded = Finding(
        path="src/a.py",
        severity=Severity.INFO,
        category="style",
        message="c",
        confidence=0.4,
    )

    result = drop_ungrounded(
        [grounded, ungrounded, second_grounded], {"src/a.py"}
    )

    assert result == [grounded, second_grounded]
