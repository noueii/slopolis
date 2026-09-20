"""Publish one target's results to GitHub (spec 10.7).

Owns the network sequence: rolling summary comment, inline comments, and the
check run, honoring the repo config's output toggles. Returns the ids it
created so the job can stamp the persisted findings.

Callers must have committed the findings and usage this publishes *before*
calling here: publishing is the one step that can be refused by a permission the
review cannot control, and a failure afterwards must not cost the run its
output.
"""

from __future__ import annotations

from dataclasses import dataclass

from slopolis_core.config.repo_config import RepoConfig
from slopolis_core.github.errors import GitHubError
from slopolis_core.review.harness import ReviewResult
from worker.deps import Publisher
from worker.jobs.publishing import (
    InlineTarget,
    check_conclusion,
    check_summary,
    check_title,
    summary_body,
)

__all__ = ["PublishOutcome", "PublishPlan", "publish"]


@dataclass(frozen=True, slots=True)
class PublishPlan:
    """Everything needed to publish one target's result set."""

    repo_full_name: str
    number: int
    head_sha: str
    result: ReviewResult
    inline: list[InlineTarget]
    repo_config: RepoConfig
    session_url: str
    status: str


@dataclass(frozen=True, slots=True)
class PublishOutcome:
    """The GitHub ids created (or ``None`` when a surface was disabled)."""

    summary_comment_id: int | None
    inline_comment_ids: list[int]
    check_run_id: int | None
    #: Why the check run was not posted, when GitHub refused it. Its own field
    #: rather than an error, because the refusal is survivable: the comments it
    #: follows are already on the pull request (spec 10.7).
    check_run_skipped: str | None = None


async def publish(publisher: Publisher, plan: PublishPlan) -> PublishOutcome:
    """Publish summary, inline comments, and the check run per the repo config.

    The order is summary, inline, check run, and only the first two are
    load-bearing: they are the review the user asked for, so a refusal there
    propagates. The check run is the advisory surface (spec 10.7), so GitHub
    refusing it is recorded in the outcome instead of raised.
    """
    output = plan.repo_config.output
    summary_id: int | None = None
    if output.summary_comment:
        summary_id = await publisher.upsert_summary_comment(
            plan.repo_full_name,
            plan.number,
            summary_body(
                result=plan.result,
                session_url=plan.session_url,
                status=plan.status,
                tokens=plan.result.tokens,
                cost_usd=plan.result.cost_usd,
            ),
            existing_comment_id=None,
        )

    inline_ids: list[int] = []
    if output.inline_comments and plan.inline:
        inline_ids = await publisher.post_inline_comments(
            plan.repo_full_name,
            plan.number,
            [target.comment for target in plan.inline],
            plan.head_sha,
        )

    check_id: int | None = None
    check_skipped: str | None = None
    if output.check_run:
        # Only this call is best effort, and it is caught broadly on purpose: a
        # refusal here means an installation without `checks: write` (a warning at
        # pre-flight, not a refusal), and other `GitHubError`s — a throttle, a
        # revoked permission — must not fail a review whose comments already
        # published either. It is not retried for the same reason: re-running the
        # publish would duplicate the comments for an artifact nobody required.
        try:
            check_id = await publisher.upsert_check_run(
                plan.repo_full_name,
                plan.head_sha,
                conclusion=check_conclusion(plan.result.findings),
                title=check_title(plan.result.findings),
                summary=check_summary(plan.result.findings),
            )
        except GitHubError as exc:
            check_skipped = f"{type(exc).__name__}: {exc}"

    return PublishOutcome(
        summary_comment_id=summary_id,
        inline_comment_ids=inline_ids,
        check_run_id=check_id,
        check_run_skipped=check_skipped,
    )
