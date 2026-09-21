"""Tests for inline comment rendering (spec 10.7).

The apply affordance is the load-bearing part: GitHub's *Commit suggestion*
replaces the cited line with whatever the block holds, so only code-shaped
suggestions may be fenced. The prose cases are real bodies the reviewer posted
to a live pull request before this guard existed.
"""

from __future__ import annotations

import pytest
from worker.jobs.publishing import inline_comments

from slopolis_core.domain import Severity
from slopolis_core.findings import Finding
from slopolis_core.github.models import InlineComment

#: Real prose the reviewer put in `suggestion` on a live pull request. Each was
#: rendered as an applyable block, offering to replace a Go line with a sentence.
_LIVE_PROSE = [
    pytest.param(
        "Make the delete and decrement atomic or compensatable; at minimum, only "
        "decrement after a successful delete and guard against concurrent/duplicate "
        "deletes (e.g., check affected rows, use a transaction/outbox, or idempotent "
        "retry).",
        id="atomic-delete",
    ),
    pytest.param(
        "Check that the user exists and the current count is greater than zero before "
        "decrementing; return a not-found or validation error otherwise.",
        id="check-before-decrement",
    ),
    pytest.param(
        "The author's post count is decremented before the post is actually deleted. If "
        "`s.repo.Delete` fails (DB error, concurrent delete, crash/retry), the user's "
        "`NumberOfPosts` is already reduced for a post that still exists, and a retry "
        "can decrement the same post again.",
        id="decrement-before-delete",
    ),
    pytest.param(
        "`r.postCounts[userID]--` decrements a missing or zero-valued entry, producing "
        "a negative `NumberOfPosts` instead of surfacing a not-found error as "
        "`IncrementPostCount` documents.",
        id="negative-count",
    ),
    pytest.param(
        "Implement this with a database update (using the passed context), e.g. "
        "`UPDATE users SET number_of_posts = number_of_posts - 1 WHERE id = $1`, "
        "handling missing rows/underflow; if this is intentionally a stub, move it to "
        "a test double.",
        id="persist-the-decrement",
    ),
]


def _body(suggestion: str | None, *, suggestions: bool = True) -> str:
    finding = Finding(
        path="internal/user/post.go",
        line=42,
        severity=Severity.ERROR,
        category="correctness",
        message="boom",
        suggestion=suggestion,
        confidence=0.9,
    )
    comments: list[InlineComment] = inline_comments(
        [finding], threshold=Severity.INFO, suggestions=suggestions
    )
    return comments[0].body


@pytest.mark.parametrize("suggestion", _LIVE_PROSE)
def test_prose_suggestion_renders_as_advice_not_a_block(suggestion: str) -> None:
    """Given prose in `suggestion`, the comment shows advice and no apply block."""
    body = _body(suggestion)

    assert "```suggestion" not in body
    assert f"**Suggested fix:** {suggestion}" in body


@pytest.mark.parametrize(
    "suggestion",
    [
        pytest.param("Guard the input before use", id="sentence-without-period"),
        pytest.param("Guard the input before use.", id="sentence-with-period"),
        pytest.param(
            "# Retry once before giving up.", id="comment-only-prose"
        ),
    ],
)
def test_prose_shapes_render_as_advice(suggestion: str) -> None:
    """Given prose without the live examples' wording, it still renders as advice."""
    body = _body(suggestion)

    assert "```suggestion" not in body
    assert f"**Suggested fix:** {suggestion}" in body


@pytest.mark.parametrize(
    "suggestion",
    [
        pytest.param("retries", id="single-identifier"),
        pytest.param("count--  # keep the counter in sync", id="code-with-comment"),
        pytest.param("# noqa", id="comment-only-terse"),
        pytest.param(
            "if err != nil {\n\treturn nil, err\n}", id="multi-line-replacement"
        ),
    ],
)
def test_code_suggestion_renders_as_an_applyable_block(suggestion: str) -> None:
    """Given code-shaped text, the comment carries an applyable suggestion block."""
    body = _body(suggestion)

    assert f"```suggestion\n{suggestion}\n```" in body
    assert "Suggested fix" not in body


@pytest.mark.parametrize(
    "suggestion",
    [
        pytest.param(None, id="null"),
        pytest.param("", id="empty"),
        pytest.param("  \n ", id="whitespace"),
    ],
)
def test_absent_suggestion_renders_nothing(suggestion: str | None) -> None:
    """Given no usable suggestion, the comment carries neither block nor advice."""
    body = _body(suggestion)

    assert "```suggestion" not in body
    assert "Suggested fix" not in body


def test_suggestions_toggle_still_withholds_every_suggestion() -> None:
    """Given `output.suggestions: false`, even code gets neither block nor advice."""
    body = _body("retries", suggestions=False)

    assert "```suggestion" not in body
    assert "Suggested fix" not in body
