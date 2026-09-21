"""Render review results into GitHub publish payloads (spec 10.7).

Pure functions only: the summary body, the inline-comment list filtered by the
severity threshold, and the check-run conclusion/summary. Keeping the rendering
here means the job orchestrates and the tests assert on exact payloads.
"""

from __future__ import annotations

from typing import NamedTuple

from slopolis_core.domain import Severity, severity_at_least
from slopolis_core.findings import Finding, is_code_shaped
from slopolis_core.github.models import InlineComment
from slopolis_core.github.publisher import SUMMARY_MARKER
from slopolis_core.review.harness import ReviewResult

__all__ = [
    "InlineTarget",
    "check_conclusion",
    "check_summary",
    "check_title",
    "inline_comments",
    "inline_targets",
    "summary_body",
]


class InlineTarget(NamedTuple):
    """A finding paired with the inline comment rendered for it."""

    source_index: int
    finding: Finding
    comment: InlineComment

_FAIL_ON = frozenset({Severity.ERROR, Severity.CRITICAL})


def summary_body(
    *,
    result: ReviewResult,
    session_url: str,
    status: str,
    tokens: int,
    cost_usd: float,
) -> str:
    """Build the rolling summary comment carried across reruns."""
    lines: list[str] = [
        SUMMARY_MARKER,
        "",
        f"- Status: **{status}**",
        f"- Session: {session_url}",
        f"- Usage: {tokens} tokens · ${cost_usd:.4f}",
    ]
    for note in result.notes:
        lines.append(f"- Note: {note}")
    if result.findings:
        lines.extend(["", "### Findings", "", _findings_table(result.findings)])
    else:
        lines.extend(["", "No findings."])
    return "\n".join(lines)


def _findings_table(findings: list[Finding]) -> str:
    """Render findings as a markdown table with location and severity."""
    rows = ["| Severity | Location | Finding |", "| --- | --- | --- |"]
    for finding in findings:
        location = finding.path
        if finding.line is not None:
            location = f"{finding.path}:{finding.line}"
        message = finding.message.replace("|", "\\|")
        rows.append(f"| {finding.severity} | {location} | {message} |")
    return "\n".join(rows)


def inline_targets(
    findings: list[Finding], *, threshold: Severity, suggestions: bool
) -> list[InlineTarget]:
    """Return line-mappable findings meeting ``threshold`` with their comments.

    Findings without a line number are excluded — they belong in the summary.
    Order is preserved so returned comment ids zip back onto these findings.
    """
    targets: list[InlineTarget] = []
    for index, finding in enumerate(findings):
        if finding.line is None or not severity_at_least(finding.severity, threshold):
            continue
        comment = InlineComment(
            path=finding.path,
            line=finding.line,
            body=_comment_body(finding, suggestions=suggestions),
        )
        targets.append(InlineTarget(source_index=index, finding=finding, comment=comment))
    return targets


def inline_comments(
    findings: list[Finding], *, threshold: Severity, suggestions: bool
) -> list[InlineComment]:
    """Return just the inline comments for the mappable findings."""
    return [target.comment for target in inline_targets(
        findings, threshold=threshold, suggestions=suggestions
    )]


def _comment_body(finding: Finding, *, suggestions: bool) -> str:
    """Render one inline comment, appending a suggestion block when enabled."""
    header = f"**{finding.severity}** · `{finding.category}`"
    body = f"{header}\n\n{finding.message}"
    section = _suggestion_section(finding.suggestion, enabled=suggestions)
    if section is not None:
        body = f"{body}\n\n{section}"
    return body


def _suggestion_section(suggestion: str | None, *, enabled: bool) -> str | None:
    """Render a suggestion, applyable only when it is code-shaped.

    GitHub's *Commit suggestion* replaces the cited line with whatever the
    block holds, so prose that reaches this field must not get the fence — it
    is shown as advice instead. A wrong fence rewrites code with a sentence; a
    missing one costs the reviewer a copy-paste (spec 10.7).
    """
    text = (suggestion or "").strip()
    if not enabled or not text:
        return None
    if is_code_shaped(text):
        return f"```suggestion\n{text}\n```"
    return f"**Suggested fix:** {text}"


def check_conclusion(findings: list[Finding]) -> str:
    """Return ``failure`` when any finding is error/critical, else ``success``."""
    if any(finding.severity in _FAIL_ON for finding in findings):
        return "failure"
    return "success"


def check_title(findings: list[Finding]) -> str:
    """Return the check-run title summarizing the finding count."""
    count = len(findings)
    return "slopolis review: 1 finding" if count == 1 else f"slopolis review: {count} findings"


def check_summary(findings: list[Finding]) -> str:
    """Return the check-run summary listing finding counts by severity."""
    if not findings:
        return "No findings."
    counts: dict[str, int] = {}
    for finding in findings:
        counts[str(finding.severity)] = counts.get(str(finding.severity), 0) + 1
    parts = ", ".join(f"{severity}: {count}" for severity, count in sorted(counts.items()))
    return f"{len(findings)} finding(s) — {parts}"
