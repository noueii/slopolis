"""Which retry a target gets: another review, or a publish of the one it has.

A manual retry normally re-runs the review — the expensive half — because the
usual cause was a condition outside the run that someone just fixed. A target
whose last attempt failed *after* the model had run (a GitHub error, with usage
recorded) is the exception: its review already exists, unposted, so re-running
the model would buy the same answer twice (spec 10.5 §Retrying a run that only
failed to publish).

The rule lives here, once, because two callers must agree on it: the retry
endpoint, which queues each target's job in the mode this module names, and
every session read, which labels the targets the UI offers so the retry button
can say what it will do. The worker never re-derives it — the mode travels with
the job.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from slopolis_core.domain import TargetStatus
from slopolis_core.github.errors import GitHubError
from slopolis_db.models import SessionTarget, SessionTargetRun

__all__ = [
    "PUBLISH",
    "RETRYABLE_TARGET_STATUSES",
    "REVIEW",
    "RetryAction",
    "attempt_retry_action",
    "latest_attempts",
    "retry_action",
    "retry_actions",
]

#: What a manual retry of a target does, and what the wire calls it (spec 10.5):
#: review the pull request again, or publish the review the last attempt bought.
RetryAction = Literal["review", "publish"]

REVIEW: RetryAction = "review"
PUBLISH: RetryAction = "publish"

#: Target statuses whose retry action a session read can name (spec 10.5 §Manual
#: retry): the run failed, or the user cancelled it, and either is re-runnable.
#: A target in any other status reports no action — including a ``queued`` one,
#: whose retryability depends on whether the queue still holds its job. That is
#: the endpoint's question (it reads the attempts and the clock to answer it), not
#: one a read that only labels buttons should ask.
RETRYABLE_TARGET_STATUSES = (TargetStatus.FAILED.value, TargetStatus.CANCELLED.value)


def attempt_retry_action(attempt: SessionTargetRun | None) -> RetryAction:
    """Return what a retry does with a target whose last attempt is ``attempt``.

    The rule once a target is known to be retryable: a last attempt that was a
    GitHub failure after the model had run (usage recorded) gets :data:`PUBLISH`,
    because the review it bought exists and only its posting failed; everything
    else gets :data:`REVIEW` — including a target that never ran an attempt, where
    there is nothing to post.

    Split from :func:`retry_action` because its two readers ask at different
    moments: a session read decides retryability and the mode together, while the
    retry endpoint has already decided exactly which targets it will re-queue —
    including a queued one whose job the queue lost, which the status rule alone
    would call unretryable — and only needs the mode from here.
    """
    if attempt is not None and attempt.tokens > 0 and _is_github_failure(attempt.error):
        return PUBLISH
    return REVIEW


def retry_action(status: str, attempt: SessionTargetRun | None) -> RetryAction | None:
    """Return what retrying ``status``' target would do, or ``None`` when it cannot.

    A target that is not retryable gets ``None``, which is the wire's absent
    action; a retryable one gets whatever its last attempt calls for, by
    :func:`attempt_retry_action`.
    """
    if status not in RETRYABLE_TARGET_STATUSES:
        return None
    return attempt_retry_action(attempt)


def _is_github_failure(error: str | None) -> bool:
    """Whether a stored attempt error names a GitHub failure.

    An attempt row keeps its failure as ``ClassName: message`` (``_error_text``
    in the worker writes it), so the class name is the only durable evidence of
    *which* error ended the attempt: the worker that raised it is long gone, and
    asking GitHub from a read path would be a live call to answer a question
    about the past.
    """
    if not error:
        return False
    name = error.split(":", 1)[0].strip()
    return name in _github_error_names()


def _github_error_names() -> frozenset[str]:
    """Every :class:`GitHubError` class name, the base and its subclasses alike.

    Derived from the hierarchy rather than listed, so a new error type is
    classified without anyone having to remember this module exists.
    """
    names: set[str] = set()
    pending: list[type[GitHubError]] = [GitHubError]
    while pending:
        error = pending.pop()
        names.add(error.__name__)
        pending.extend(error.__subclasses__())
    return frozenset(names)


_GITHUB_ERROR_NAMES = _github_error_names()


async def latest_attempts(
    db: AsyncSession, target_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, SessionTargetRun]:
    """Return each target's most recent attempt, keyed by target id.

    One query for a whole page, and none at all when there is nothing to ask
    about: a read with no retryable target — a live session, the dashboard —
    must not grow a round trip because the wire carries a field for failures.
    """
    if not target_ids:
        return {}
    rows = await db.scalars(
        select(SessionTargetRun)
        .where(SessionTargetRun.target_id.in_(target_ids))
        .order_by(SessionTargetRun.target_id, SessionTargetRun.attempt)
    )
    latest: dict[uuid.UUID, SessionTargetRun] = {}
    for row in rows:
        # Attempts ascend, so the target's last row written wins.
        latest[row.target_id] = row
    return latest


async def retry_actions(
    db: AsyncSession, targets: Sequence[SessionTarget]
) -> dict[uuid.UUID, RetryAction | None]:
    """Return each target's retry action, or ``None`` when it is not retryable.

    Only retryable targets read their attempts: a target the queue owns, or one
    that has finished, cannot be retried whatever its attempts say, so its action
    is ``None`` without a lookup (spec 10.5).
    """
    retryable = [
        target.id for target in targets if target.status in RETRYABLE_TARGET_STATUSES
    ]
    latest = await latest_attempts(db, retryable)
    return {
        target.id: retry_action(target.status, latest.get(target.id))
        for target in targets
    }
