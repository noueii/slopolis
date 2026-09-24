"""Publish one target's results to GitHub (spec 10.7).

Owns the network sequence: rolling summary comment, inline comments, and the
check run, honoring the repo config's output toggles. Each line comment is
reconciled against what the pull request already holds and stamped the moment it
exists — with the comment id GitHub assigned and the hunk it returned for it — so
a run that dies mid-publish neither stacks a second copy of a comment nor leaves
the app reading it as unposted.

Callers must have committed the findings and usage this publishes *before*
calling here: publishing is the one step that can be refused by a permission the
review cannot control, and a failure afterwards must not cost the run its
output.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from slopolis_core.config.repo_config import RepoConfig
from slopolis_core.github.errors import GitHubError
from slopolis_core.github.models import InlineComment, ReviewComment
from slopolis_core.review.harness import ReviewResult
from worker.deps import Publisher
from worker.jobs.publishing import (
    InlineTarget,
    check_conclusion,
    check_summary,
    check_title,
    summary_body,
)

__all__ = ["InlinePost", "PublishOutcome", "PublishPlan", "StampInline", "publish"]

_LOG = logging.getLogger("worker.jobs.publish")


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
class InlinePost:
    """One finding's line comment, as it stands on the pull request.

    ``source_index`` addresses the finding in the list that was published, which
    is what lets a caller stamp the row a comment belongs to even when some of
    this publish's comments were adopted rather than posted (spec 10.7).

    ``diff_hunk`` is GitHub's own hunk for that comment — the text its UI renders
    above the comment — carried through from whichever comment this publish
    ended up with, so the stamp can record the code the finding is about.
    ``None`` when GitHub returned no hunk for the comment.
    """

    source_index: int
    comment_id: int
    diff_hunk: str | None


#: Records that a finding's comment exists, with GitHub's own hunk for it, called
#: the moment it does — adopted or freshly posted. The caller commits inside it:
#: an uncommitted link does not survive the rollback a later failure ends the
#: attempt with.
StampInline = Callable[[InlinePost], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class PublishOutcome:
    """The GitHub ids created (or ``None`` when a surface was disabled)."""

    summary_comment_id: int | None
    #: Every finding whose comment is on the pull request now, with the id it
    #: carries there and GitHub's hunk for it — whether this publish posted the
    #: comment or adopted it.
    inline_posts: list[InlinePost]
    check_run_id: int | None
    #: Why the check run was not posted, when GitHub refused it. Its own field
    #: rather than an error, because the refusal is survivable: the comments it
    #: follows are already on the pull request (spec 10.7).
    check_run_skipped: str | None = None


async def publish(
    publisher: Publisher, plan: PublishPlan, *, stamp: StampInline
) -> PublishOutcome:
    """Publish summary, inline comments, and the check run per the repo config.

    The order is summary, inline, check run, and only the first two are
    load-bearing: they are the review the user asked for, so a refusal there
    propagates. The check run is the advisory surface (spec 10.7), so GitHub
    refusing it is recorded in the outcome instead of raised.

    ``stamp`` runs as each line comment lands, before the check run and before the
    next comment: the check run is best effort and a later comment can be refused,
    and a link recorded after either would be a link the failure path loses.
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
            existing_comment_id=await _existing_summary_id(publisher, plan),
        )

    inline_posts: list[InlinePost] = []
    if output.inline_comments and plan.inline:
        inline_posts = await _publish_inline(publisher, plan, stamp)

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
        inline_posts=inline_posts,
        check_run_id=check_id,
        check_run_skipped=check_skipped,
    )


async def _publish_inline(
    publisher: Publisher, plan: PublishPlan, stamp: StampInline
) -> list[InlinePost]:
    """Get every finding's line comment onto the pull request, stamping as it goes.

    One comment per call, not one call for all of them: ``post_inline_comments``
    returns its comments only when every comment in its list was accepted, so a
    refusal on a later comment would discard the comments already on the pull
    request — exactly the link this is here to keep. Each comment is stamped the
    moment it exists, with the id and the hunk GitHub gave it, and whatever this
    App already has at the same path and line is adopted instead of posted
    (spec 10.7 §Publishing is idempotent per finding).
    """
    comments = [target.comment for target in plan.inline]
    adopted = await _reconcile(publisher, plan, comments)
    posts: list[InlinePost] = []
    for target, adopted_comment in zip(plan.inline, adopted, strict=True):
        posted = (
            adopted_comment
            if adopted_comment is not None
            else await _post_one(publisher, plan, target.comment)
        )
        post = InlinePost(
            source_index=target.source_index,
            comment_id=posted.id,
            diff_hunk=posted.diff_hunk,
        )
        await stamp(post)
        posts.append(post)
    return posts


async def _post_one(
    publisher: Publisher, plan: PublishPlan, comment: InlineComment
) -> ReviewComment:
    """Post one line comment and return it as GitHub holds it.

    One comment per call, so the returned comment is the first — and only — one
    in the list.
    """
    created = await publisher.post_inline_comments(
        plan.repo_full_name, plan.number, [comment], plan.head_sha
    )
    return created[0]


async def _reconcile(
    publisher: Publisher, plan: PublishPlan, comments: list[InlineComment]
) -> list[ReviewComment | None]:
    """The comment that already carries each finding, or ``None`` to post one.

    A refused read is not a refused review. Reconciliation only prevents
    duplicates; the review is the thing the user asked for, so a pull request
    whose comments cannot be read is published the way it was before
    reconciliation existed — every comment posted — with the reason logged
    (spec 10.7).
    """
    try:
        return await publisher.reconcile_inline_comments(
            plan.repo_full_name, plan.number, comments
        )
    except GitHubError as exc:
        _LOG.warning(
            "inline comment reconciliation failed; posting every comment",
            extra={
                "repo_full_name": plan.repo_full_name,
                "number": plan.number,
                "reason": f"{type(exc).__name__}: {exc}",
            },
        )
        return [None] * len(comments)


async def _existing_summary_id(publisher: Publisher, plan: PublishPlan) -> int | None:
    """The summary comment this publish should edit, or ``None`` to post a new one.

    A lookup GitHub refuses is not a review GitHub refused: the summary is one
    artifact among the ones this publish posts, and a second copy of it is a
    smaller loss than a review that never reaches the pull request. So the
    failure degrades to a create, with a warning naming the reason — the next
    publish cannot roll a comment it never learned the id of (spec 10.7).
    """
    try:
        return await publisher.find_summary_comment(plan.repo_full_name, plan.number)
    except GitHubError as exc:
        _LOG.warning(
            "summary comment lookup failed; posting a new one",
            extra={
                "repo_full_name": plan.repo_full_name,
                "number": plan.number,
                "reason": f"{type(exc).__name__}: {exc}",
            },
        )
        return None
