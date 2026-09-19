"""Canonical domain enums and ordering helpers.

These mirror `apps/web/src/api/contract.ts` one-to-one. The UI contract and the
backend domain must agree on the exact string values, so every enum is a
`StrEnum` whose values are the wire strings.
"""

from enum import StrEnum

__all__ = [
    "SEVERITY_ORDER",
    "ProviderKind",
    "SessionStatus",
    "Severity",
    "TargetStatus",
    "meets_threshold",
    "severity_at_least",
]


class Severity(StrEnum):
    """Finding severities produced by the review harness (spec 10.6)."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class SessionStatus(StrEnum):
    """Lifecycle of a whole review session (spec 10.5 / 10.8)."""

    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TargetStatus(StrEnum):
    """Lifecycle of one PR target inside a session."""

    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


class ProviderKind(StrEnum):
    """Model gateway flavors supported by the workspace (spec 10.2)."""

    LITELLM = "litellm"
    OPENAI_COMPATIBLE = "openai_compatible"


#: Monotonic rank per severity. Higher means more severe.
SEVERITY_ORDER: dict[Severity, int] = {
    Severity.INFO: 0,
    Severity.WARNING: 1,
    Severity.ERROR: 2,
    Severity.CRITICAL: 3,
}


def severity_at_least(value: Severity, threshold: Severity) -> bool:
    """Return True when ``value`` is at or above ``threshold``.

    A finding is posted only when its severity meets the configured
    ``severity_threshold`` (spec 11).
    """
    return SEVERITY_ORDER[value] >= SEVERITY_ORDER[threshold]


def meets_threshold(severity: Severity, threshold: Severity) -> bool:
    """Alias for :func:`severity_at_least` phrased from the finding side."""
    return severity_at_least(severity, threshold)
