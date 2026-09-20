"""End-to-end proof of the Phase 0 gate: one PR in, one real review out.

The test drives the real server routes (``POST /api/reviews/preflight`` then
``POST /api/sessions``) and then invokes the real
``worker.jobs.review_target.review_target`` job against the same database.
Fakes exist only at the process boundaries: GitHub, the LLM gateway, the live
model check, the ARQ Redis pool, and the publisher's network calls.

Hermetic by construction: temporary SQLite, ASGI transport. No Docker, no
network, no Redis.
"""

from __future__ import annotations

import uuid

from harness import Env
from sqlalchemy import select
from support import (
    GROUNDED_PATH,
    HEAD_SHA,
    MODEL_ID,
    PR_NUMBER,
    PR_URL,
    REPO_FULL_NAME,
)
from worker.jobs.review_target import review_target

from slopolis_db.models import Finding, ReviewSession, SessionTarget, UsageRecord

_EXPECTED_TOKENS = 168


async def test_one_pr_session_produces_a_real_review(env: Env) -> None:
    """A single PR flows pre-flight -> queue -> real worker -> GitHub, end to end."""
    # Given a seeded workspace whose review role resolves a real catalog model
    # (the env fixture built the graph, the app overrides, and the worker ctx).

    # When pre-flight validates the one pasted PR link
    preflight = await env.client.post(
        "/api/reviews/preflight", json={"prUrls": [PR_URL]}
    )

    # Then the link is valid and the live model check ran against the resolved model
    assert preflight.status_code == 200
    preflight_body = preflight.json()
    assert [item["url"] for item in preflight_body["valid"]] == [PR_URL]
    assert preflight_body["invalid"] == []
    assert env.live_check.models == [MODEL_ID]

    # When the session is created
    created = await env.client.post(
        "/api/sessions",
        json={"prUrls": [PR_URL], "prompt": "Focus on auth bugs"},
    )

    # Then exactly one target is persisted and its id is captured
    assert created.status_code == 201
    created_body = created.json()
    assert created_body["status"] == "queued"
    assert created_body["targetCount"] == 1
    session_id = uuid.UUID(created_body["id"])

    async with env.db.maker() as session:
        target = await session.scalar(
            select(SessionTarget).where(SessionTarget.session_id == session_id)
        )
        assert target is not None
        target_id = str(target.id)

    # ... and exactly one review_target job was enqueued for (session, target),
    # in review mode: a submission never has a review to re-publish
    assert env.pool.jobs == [("review_target", (str(session_id), target_id, "review"))]

    # When the real worker job runs against the same database
    await review_target(env.ctx, str(session_id), target_id)

    target_uuid = uuid.UUID(target_id)
    async with env.db.maker() as session:
        findings = list(
            (
                await session.scalars(
                    select(Finding).where(Finding.target_id == target_uuid)
                )
            ).all()
        )
        usage = list(
            (
                await session.scalars(
                    select(UsageRecord).where(UsageRecord.target_id == target_uuid)
                )
            ).all()
        )
        target_row = await session.get(SessionTarget, target_uuid)
        session_row = await session.get(ReviewSession, session_id)

    # Then only the grounded finding persisted; the ungrounded one is dropped
    assert len(findings) == 1
    assert findings[0].path == GROUNDED_PATH
    assert findings[0].posted is True
    assert findings[0].github_comment_id == 201

    # ... exactly one usage record for the model the assignment resolved
    assert len(usage) == 1
    assert usage[0].model_id == MODEL_ID
    assert usage[0].total_tokens == _EXPECTED_TOKENS

    # ... the target and the parent session both finish done
    assert target_row is not None
    assert target_row.status == "done"
    assert session_row is not None
    assert session_row.status == "done"

    # ... the publisher got the summary, only the grounded inline, and a failing check
    assert len(env.publisher.summaries) == 1
    repo_name, number, summary, existing_id = env.publisher.summaries[0]
    assert (repo_name, number, existing_id) == (REPO_FULL_NAME, PR_NUMBER, None)
    assert str(session_id) in summary
    assert GROUNDED_PATH in summary

    assert len(env.publisher.inlines) == 1
    _, inline_number, comments, commit_id = env.publisher.inlines[0]
    assert inline_number == PR_NUMBER
    assert commit_id == HEAD_SHA
    assert [comment.path for comment in comments] == [GROUNDED_PATH]

    assert len(env.publisher.checks) == 1
    _, head_sha, conclusion, title, _ = env.publisher.checks[0]
    assert head_sha == HEAD_SHA
    assert conclusion == "failure"
    assert title == "slopolis review: 1 finding"

    # ... and the gateway was asked for the workspace-assigned model, nothing hardcoded
    assert env.llm.models == [MODEL_ID]


async def test_a_republish_edits_the_summary_comment(env: Env) -> None:
    """A pull request that already carries the session's summary gets it updated."""
    # Given a pull request that an earlier publish already commented on
    env.publisher.existing_summary_id = 101

    # When the one pasted PR link is queued and the real worker job runs
    await env.client.post("/api/reviews/preflight", json={"prUrls": [PR_URL]})
    created = await env.client.post(
        "/api/sessions",
        json={"prUrls": [PR_URL], "prompt": "Focus on auth bugs"},
    )
    assert created.status_code == 201, created.text
    session_id = uuid.UUID(created.json()["id"])
    async with env.db.maker() as session:
        target = await session.scalar(
            select(SessionTarget).where(SessionTarget.session_id == session_id)
        )
        assert target is not None
        target_id = str(target.id)

    await review_target(env.ctx, str(session_id), target_id)

    # Then one comment was published, and it was the one already on the pull
    # request — an edit, not a second summary beside the first
    assert len(env.publisher.summaries) == 1
    repo_name, number, _, existing_id = env.publisher.summaries[0]
    assert (repo_name, number, existing_id) == (REPO_FULL_NAME, PR_NUMBER, 101)
