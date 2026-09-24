"""Session detail, rename, cancel, and manual retry (spec 10.5 §Manual retry)."""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from app.config import get_app_settings
from app.routers.sessions import QUEUE_STALE_AFTER_S
from app.services.repo_access import RepoAccessChecker, RepoAccessUnavailable
from sqlalchemy import select

from slopolis_core.settings import get_settings as get_core_settings
from slopolis_db.models import (
    Finding,
    Repository,
    ReviewSession,
    SessionTarget,
    SessionTargetRun,
    User,
    Workspace,
)

from .conftest import (
    ApiHarness,
    add_installation,
    seed_empty_workspace,
    seed_session,
)


async def test_get_session_returns_targets(
    seeded: Any, build_harness: Any
) -> None:
    # Given a seeded session
    harness: ApiHarness = await build_harness(user_id=seeded.user_id)

    # When it is fetched by id
    response = await harness.client.get(f"/api/sessions/{seeded.session_id}")

    # Then the full session with its target is returned
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(seeded.session_id)
    assert body["status"] == "queued"
    assert body["prompt"] == "Focus on security"
    assert body["targets"][0]["headBranch"] == "fix/branch"


async def test_get_session_carries_its_targets_findings(
    seeded: Any, session_factory: Any, build_harness: Any
) -> None:
    # Given a session whose target carries three findings: two the publisher
    # posted as inline comments, each with the hunk GitHub shows above it, and
    # one it never got to post — no diff line, so nowhere to anchor a comment
    hunk_a = "@@ -8,7 +8,9 @@ def load()\n context\n-old = read()\n+index = items[0]\n context"
    hunk_b = "@@ -1,4 +1,5 @@\n import os\n+MAX = 10\n context"
    async with session_factory() as session:
        target = await session.scalar(
            select(SessionTarget).where(SessionTarget.session_id == seeded.session_id)
        )
        repository = await session.get(Repository, seeded.repository_id)
        assert target is not None and repository is not None
        session.add_all(
            [
                Finding(
                    target_id=target.id,
                    path="src/a.py",
                    line=10,
                    severity="error",
                    category="correctness",
                    message="unguarded index",
                    confidence=0.9,
                    github_comment_id=501,
                    posted=True,
                    diff_hunk=hunk_a,
                ),
                Finding(
                    target_id=target.id,
                    path="src/b.py",
                    line=4,
                    severity="info",
                    category="style",
                    message="name the constant",
                    confidence=0.9,
                    github_comment_id=502,
                    posted=True,
                    diff_hunk=hunk_b,
                ),
                Finding(
                    target_id=target.id,
                    path="src/c.py",
                    line=None,
                    severity="warning",
                    category="performance",
                    message="repeated scan",
                    confidence=0.9,
                    posted=False,
                ),
            ]
        )
        await session.commit()
        posted = {
            "src/a.py": (
                f"https://github.com/{repository.full_name}/pull/{target.number}"
                "#discussion_r501"
            ),
            "src/b.py": (
                f"https://github.com/{repository.full_name}/pull/{target.number}"
                "#discussion_r502"
            ),
        }
    harness: ApiHarness = await build_harness(user_id=seeded.user_id)

    # When the session detail is fetched
    response = await harness.client.get(f"/api/sessions/{seeded.session_id}")

    # Then the target carries its findings most severe first, each linking the
    # comment it was posted as — and nothing when it was never posted
    assert response.status_code == 200
    findings = response.json()["targets"][0]["findings"]
    assert [finding["severity"] for finding in findings] == [
        "error",
        "warning",
        "info",
    ]
    assert [finding["path"] for finding in findings] == [
        "src/a.py",
        "src/c.py",
        "src/b.py",
    ]
    assert [finding["commentUrl"] for finding in findings] == [
        posted["src/a.py"],
        None,
        posted["src/b.py"],
    ]
    # ... each posted finding names no author — this deployment configures no
    # App slug — but dates the comment the publisher wrote and carries the hunk
    # GitHub renders above it; the unposted one carries none of the four
    by_path = {finding["path"]: finding for finding in findings}
    for path in ("src/a.py", "src/b.py"):
        assert by_path[path]["author"] is None
        assert by_path[path]["postedAt"] is not None
    assert by_path["src/a.py"]["diffHunk"] == hunk_a
    assert by_path["src/b.py"]["diffHunk"] == hunk_b
    assert by_path["src/c.py"]["author"] is None
    assert by_path["src/c.py"]["postedAt"] is None
    assert by_path["src/c.py"]["diffHunk"] is None

    # ... while a page of sessions carries no findings at all: the list read
    # must not ship every finding of every session
    listed = await harness.client.get("/api/sessions")
    assert listed.status_code == 200
    targets = [
        target for item in listed.json()["items"] for target in item["targets"]
    ]
    assert targets
    assert all(target["findings"] is None for target in targets)


async def test_get_session_names_the_configured_app_as_comment_author(
    seeded: Any, session_factory: Any, build_harness: Any, monkeypatch: Any
) -> None:
    # Given a session whose target carries one posted finding, and a deployment
    # that knows the App's slug
    async with session_factory() as session:
        target = await session.scalar(
            select(SessionTarget).where(SessionTarget.session_id == seeded.session_id)
        )
        assert target is not None
        session.add(
            Finding(
                target_id=target.id,
                path="src/a.py",
                line=10,
                severity="error",
                category="correctness",
                message="unguarded index",
                confidence=0.9,
                github_comment_id=501,
                posted=True,
            )
        )
        await session.commit()

    core = get_core_settings().model_copy(
        update={"github_app_slug": "slopolis-test"}
    )
    monkeypatch.setattr("app.config.get_core_settings", lambda: core)
    harness: ApiHarness = await build_harness(
        user_id=seeded.user_id, settings=get_app_settings()
    )

    # When the session detail is fetched
    response = await harness.client.get(f"/api/sessions/{seeded.session_id}")

    # Then the finding names the configured App as the comment's author — read
    # from configuration, never a GitHub lookup
    assert response.status_code == 200
    findings = response.json()["targets"][0]["findings"]
    assert [finding["author"] for finding in findings] == ["slopolis-test[bot]"]


async def test_get_unknown_session_is_404(
    seeded: Any, build_harness: Any
) -> None:
    # Given a workspace without the requested session
    harness: ApiHarness = await build_harness(user_id=seeded.user_id)

    # When an unknown id is fetched
    response = await harness.client.get(f"/api/sessions/{uuid.uuid4()}")

    # Then the standard 404 envelope is returned
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "session_not_found"


async def test_patch_session_renames(seeded: Any, build_harness: Any) -> None:
    # Given a seeded session
    harness: ApiHarness = await build_harness(user_id=seeded.user_id)

    # When its title is patched
    response = await harness.client.patch(
        f"/api/sessions/{seeded.session_id}", json={"title": "Renamed review"}
    )

    # Then the new title is persisted in the response
    assert response.status_code == 200
    assert response.json()["title"] == "Renamed review"


async def test_patch_session_requires_a_value(
    seeded: Any, build_harness: Any
) -> None:
    # Given a seeded session
    harness: ApiHarness = await build_harness(user_id=seeded.user_id)

    # When an empty patch is sent
    response = await harness.client.patch(
        f"/api/sessions/{seeded.session_id}", json={"title": "   "}
    )

    # Then a 422 title error is returned
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "title_required"


async def test_cancel_session_then_conflict(
    seeded: Any, build_harness: Any
) -> None:
    # Given a queued session
    harness: ApiHarness = await build_harness(user_id=seeded.user_id)

    # When it is cancelled and then cancelled again
    first = await harness.client.post(f"/api/sessions/{seeded.session_id}/cancel")
    second = await harness.client.post(f"/api/sessions/{seeded.session_id}/cancel")

    # Then the first wins and the second reports a conflict
    assert first.status_code == 200
    assert first.json()["status"] == "cancelled"
    assert first.json()["targets"][0]["status"] == "cancelled"
    assert first.json()["finishedAt"] is not None
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "session_not_cancellable"


# --- manual retry (spec 10.5) -----------------------------------------------

#: What the worker stores when GitHub refuses a write after the model has run —
#: ``ClassName: message``, the format the retry rule classifies from. The
#: throttled publish is what a target that only failed to publish usually hit.
_GITHUB_FAILURE = "GitHubRateLimitError: GitHub rate limit hit during upsert_summary_comment"

#: How long a test leaves a target queued when it wants that target's job read as
#: lost: comfortably past the server's threshold, expressed against it so the test
#: follows the rule rather than a copy of its number.
_LOST_QUEUE_AGE = dt.timedelta(seconds=QUEUE_STALE_AFTER_S * 3)


class FakeRepoProbe:
    """Answers the viewer's repository read with a fixed verdict.

    ``on_read`` runs before the verdict, so a test can commit what another writer
    would have committed while this request was still nowhere near its own write.
    """

    def __init__(
        self,
        *,
        readable: bool = True,
        unavailable: bool = False,
        on_read: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self.readable = readable
        self.unavailable = unavailable
        self.on_read = on_read
        self.calls: list[str] = []

    async def user_can_read(self, *, token: str, repo_full_name: str) -> bool:
        self.calls.append(repo_full_name)
        if self.on_read is not None:
            await self.on_read()
        if self.unavailable:
            raise RepoAccessUnavailable("github is unreachable")
        return self.readable


def make_checker(probe: FakeRepoProbe) -> RepoAccessChecker:
    """Checker over ``probe``, with a token source standing in for the vault."""
    return RepoAccessChecker(probe=probe, tokens=lambda _user: "gho_reader")


async def add_member(
    session_factory: Any, workspace_id: uuid.UUID, *, github_id: int = 1002
) -> uuid.UUID:
    """Add a workspace member who triggered nothing; return their id."""
    async with session_factory() as session:
        user = User(
            workspace_id=workspace_id,
            github_id=github_id,
            handle="reader",
            name="Reader",
            avatar_url=None,
        )
        session.add(user)
        await session.commit()
        return user.id


async def set_state(
    session_factory: Any,
    session_id: uuid.UUID,
    *,
    status: str,
    target_status: str | None = None,
) -> None:
    """Force a session — and optionally all its targets — into a recorded state.

    ``finished_at`` is part of the state: a session that reached a terminal
    status has one, which is what a retry has to clear.
    """
    async with session_factory() as session:
        row = await session.get(ReviewSession, session_id)
        assert row is not None
        row.status = status
        row.finished_at = row.created_at
        if target_status is not None:
            targets = (
                await session.scalars(
                    select(SessionTarget).where(SessionTarget.session_id == session_id)
                )
            ).all()
            for target in targets:
                target.status = target_status
        await session.commit()


async def set_target_state(
    session_factory: Any, target_id_: uuid.UUID, status: str
) -> None:
    """Force one target's status, for the mixed sessions a retry has to judge."""
    async with session_factory() as session:
        row = await session.get(SessionTarget, target_id_)
        assert row is not None
        row.status = status
        await session.commit()


async def add_target(
    session_factory: Any,
    session_id: uuid.UUID,
    repository_id: uuid.UUID,
    *,
    number: int,
    status: str,
) -> uuid.UUID:
    """Add one more PR target to a session and return its id."""
    async with session_factory() as session:
        target = SessionTarget(
            session_id=session_id,
            repository_id=repository_id,
            number=number,
            title=f"fix: change {number}",
            url=f"https://github.com/acme/api/pull/{number}",
            head_branch=f"fix/{number}",
            status=status,
        )
        session.add(target)
        await session.commit()
        return target.id


async def target_id(
    session_factory: Any, session_id: uuid.UUID, number: int
) -> uuid.UUID:
    """Return the id of one target of a session."""
    async with session_factory() as session:
        found = await session.scalar(
            select(SessionTarget.id).where(
                SessionTarget.session_id == session_id, SessionTarget.number == number
            )
        )
        assert found is not None
        return found


async def add_attempt(
    session_factory: Any,
    target_id_: uuid.UUID,
    *,
    error: str = "provider timed out",
    tokens: int = 0,
    status: str = "failed",
) -> uuid.UUID:
    """Record one attempt for a target; return the run's id.

    ``error`` and ``tokens`` are the two things the retry rule reads (spec 10.5):
    a GitHub failure that recorded usage is a review the target already owns.
    ``status`` is the third a *queue* rule reads: an attempt still ``running`` is a
    worker on the target, so a retry has to leave it alone.
    """
    async with session_factory() as session:
        run = SessionTargetRun(
            target_id=target_id_,
            attempt=1,
            status=status,
            error=error,
            tokens=tokens,
        )
        session.add(run)
        await session.commit()
        return run.id


async def age_target(
    session_factory: Any, target_id_: uuid.UUID, *, age: dt.timedelta
) -> None:
    """Backdate a target's last write by ``age``, as if it had waited that long.

    ``updated_at`` is what a retry measures a queued target's staleness from, and
    only a direct write can put it in the past — which is the point of testing it.
    """
    async with session_factory() as session:
        row = await session.get(SessionTarget, target_id_)
        assert row is not None
        row.updated_at = dt.datetime.now(dt.UTC) - age
        await session.commit()


async def queue_session_and_targets(
    session_factory: Any, session_id: uuid.UUID
) -> None:
    """Commit what a retry that got there first leaves behind: all of it queued."""
    async with session_factory() as session:
        row = await session.get(ReviewSession, session_id)
        assert row is not None
        row.status = "queued"
        row.finished_at = None
        queued = (
            await session.scalars(
                select(SessionTarget).where(SessionTarget.session_id == session_id)
            )
        ).all()
        for target in queued:
            target.status = "queued"
        await session.commit()


async def test_retry_requeues_failed_targets_and_enqueues_each(
    seeded: Any, session_factory: Any, build_harness: Any
) -> None:
    # Given a failed session with two failed targets and an attempt on record
    first = await target_id(session_factory, seeded.session_id, 7)
    second = await add_target(
        session_factory, seeded.session_id, seeded.repository_id, number=8, status="failed"
    )
    await set_state(session_factory, seeded.session_id, status="failed", target_status="failed")
    attempt = await add_attempt(session_factory, first)
    harness: ApiHarness = await build_harness(user_id=seeded.user_id)

    # When the session is retried without naming targets
    response = await harness.client.post(f"/api/sessions/{seeded.session_id}/retry")

    # Then both targets and the session are queued again, with no finish time
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "queued"
    assert body["finishedAt"] is None
    assert [target["status"] for target in body["targets"]] == ["queued", "queued"]

    # And exactly one review_target job per target carries the submit path's args,
    # in review mode: neither attempt says a review is already on hand
    assert set(harness.pool.jobs) == {
        ("review_target", (str(seeded.session_id), str(first), "review")),
        ("review_target", (str(seeded.session_id), str(second), "review")),
    }

    # And the earlier attempt survives as history
    async with session_factory() as session:
        runs = (
            await session.scalars(
                select(SessionTargetRun).where(SessionTargetRun.target_id == first)
            )
        ).all()
    assert [run.id for run in runs] == [attempt]


async def test_retry_reports_and_enqueues_a_publish_retry(
    seeded: Any, session_factory: Any, build_harness: Any
) -> None:
    # Given a failed session whose target failed to publish a review it had
    # already paid for — a GitHub error with usage recorded on the attempt
    target = await target_id(session_factory, seeded.session_id, 7)
    await set_state(session_factory, seeded.session_id, status="failed", target_status="failed")
    await add_attempt(session_factory, target, error=_GITHUB_FAILURE, tokens=120)
    harness: ApiHarness = await build_harness(user_id=seeded.user_id)

    # When the session is read
    detail = await harness.client.get(f"/api/sessions/{seeded.session_id}")

    # Then the target says the retry will re-publish rather than review again
    assert detail.status_code == 200
    assert detail.json()["targets"][0]["retryAction"] == "publish"

    # When it is retried
    response = await harness.client.post(f"/api/sessions/{seeded.session_id}/retry")

    # Then the job is enqueued in publish mode, so the model never runs again
    assert response.status_code == 200
    assert harness.pool.jobs == [
        ("review_target", (str(seeded.session_id), str(target), "publish"))
    ]


async def test_retry_reviews_when_the_last_attempt_has_no_review_on_hand(
    seeded: Any, session_factory: Any, build_harness: Any
) -> None:
    # Given two failed targets: one whose last attempt was a GitHub failure that
    # recorded no usage (the model never finished), and one whose failure was not
    # GitHub's at all
    unpriced = await target_id(session_factory, seeded.session_id, 7)
    unparsed = await add_target(
        session_factory, seeded.session_id, seeded.repository_id, number=8, status="failed"
    )
    await set_state(session_factory, seeded.session_id, status="failed", target_status="failed")
    await add_attempt(session_factory, unpriced, error=_GITHUB_FAILURE, tokens=0)
    await add_attempt(
        session_factory, unparsed, error="ModelNotConfiguredError: no model", tokens=120
    )
    harness: ApiHarness = await build_harness(user_id=seeded.user_id)

    # When the session is read
    detail = await harness.client.get(f"/api/sessions/{seeded.session_id}")

    # Then neither target has a review to publish, so both say review
    assert detail.status_code == 200
    actions = {target["id"]: target["retryAction"] for target in detail.json()["targets"]}
    assert actions == {str(unpriced): "review", str(unparsed): "review"}

    # When it is retried
    response = await harness.client.post(f"/api/sessions/{seeded.session_id}/retry")

    # Then both jobs are enqueued in review mode
    assert response.status_code == 200
    assert sorted(harness.pool.jobs) == sorted(
        [
            ("review_target", (str(seeded.session_id), str(unpriced), "review")),
            ("review_target", (str(seeded.session_id), str(unparsed), "review")),
        ]
    )


async def test_a_target_that_is_not_retryable_reports_no_retry_action(
    seeded: Any, session_factory: Any, build_harness: Any
) -> None:
    # Given a done target whose history holds a GitHub failure with usage — the
    # same attempt a failed target would carry — and its session finished
    target = await target_id(session_factory, seeded.session_id, 7)
    await set_state(session_factory, seeded.session_id, status="done", target_status="done")
    await add_attempt(session_factory, target, error=_GITHUB_FAILURE, tokens=120)
    harness: ApiHarness = await build_harness(user_id=seeded.user_id)

    # When the session is read
    detail = await harness.client.get(f"/api/sessions/{seeded.session_id}")

    # Then there is no action to promise, because the target cannot be retried
    assert detail.status_code == 200
    assert detail.json()["targets"][0]["retryAction"] is None


async def test_retry_enqueues_a_target_once_across_two_calls(
    seeded: Any, session_factory: Any, build_harness: Any
) -> None:
    # Given a failed session with two failed targets
    first = await target_id(session_factory, seeded.session_id, 7)
    second = await add_target(
        session_factory, seeded.session_id, seeded.repository_id, number=8, status="failed"
    )
    await set_state(session_factory, seeded.session_id, status="failed", target_status="failed")
    harness: ApiHarness = await build_harness(user_id=seeded.user_id)

    # When the same session is retried twice — the double POST a client that sends
    # the request again would produce; the retry's session-row lock is what makes
    # the second request wait for the first instead of racing it
    accepted = await harness.client.post(f"/api/sessions/{seeded.session_id}/retry")
    refused = await harness.client.post(
        f"/api/sessions/{seeded.session_id}/retry",
        json={"targetIds": [str(first), str(second)]},
    )

    # Then the first call queues both targets and the second finds the queue owns
    # them rather than queueing them again
    assert accepted.status_code == 200
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "target_running"

    # And each target was enqueued exactly once across the two calls
    assert sorted(harness.pool.jobs) == sorted(
        [
            ("review_target", (str(seeded.session_id), str(first), "review")),
            ("review_target", (str(seeded.session_id), str(second), "review")),
        ]
    )


async def test_retry_leaves_a_session_with_a_running_target_running(
    seeded: Any, session_factory: Any, build_harness: Any
) -> None:
    # Given a live session: one target failed, its sibling still running
    failed = await target_id(session_factory, seeded.session_id, 7)
    running = await add_target(
        session_factory, seeded.session_id, seeded.repository_id, number=8, status="running"
    )
    await set_state(session_factory, seeded.session_id, status="running", target_status="failed")
    await set_target_state(session_factory, running, "running")
    harness: ApiHarness = await build_harness(user_id=seeded.user_id)

    # When only the failed target is retried
    response = await harness.client.post(
        f"/api/sessions/{seeded.session_id}/retry", json={"targetIds": [str(failed)]}
    )

    # Then it goes back on the queue, and the session stays the live one it is
    # rather than being moved backwards to queued
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "running"
    assert body["finishedAt"] is None
    statuses = {target["id"]: target["status"] for target in body["targets"]}
    assert statuses[str(failed)] == "queued"
    assert statuses[str(running)] == "running"
    assert harness.pool.jobs == [
        ("review_target", (str(seeded.session_id), str(failed), "review"))
    ]


async def test_retry_judges_the_targets_it_reads_under_the_lock(
    seeded: Any, session_factory: Any, build_harness: Any
) -> None:
    # Given a failed session, and a request overtaken mid-flight by a retry whose
    # commit queued both targets — the state the row lock waits for, injected here
    # because the test database has no row locks to wait on
    first = await target_id(session_factory, seeded.session_id, 7)
    second = await add_target(
        session_factory, seeded.session_id, seeded.repository_id, number=8, status="failed"
    )
    await set_state(session_factory, seeded.session_id, status="failed", target_status="failed")
    reader_id = await add_member(session_factory, seeded.workspace_id)
    harness: ApiHarness = await build_harness(
        user_id=reader_id,
        repo_access=make_checker(
            FakeRepoProbe(
                on_read=lambda: queue_session_and_targets(
                    session_factory, seeded.session_id
                )
            )
        ),
    )

    # When that request goes on to retry the same two targets
    response = await harness.client.post(
        f"/api/sessions/{seeded.session_id}/retry",
        json={"targetIds": [str(first), str(second)]},
    )

    # Then it judges them on the statuses it read under the lock rather than the
    # snapshot it loaded before it, so targets the queue already owns are refused
    # instead of being enqueued a second time
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "target_running"
    assert harness.pool.jobs == []


async def test_retry_requeues_only_a_requested_cancelled_target(
    seeded: Any, session_factory: Any, build_harness: Any
) -> None:
    # Given a cancelled session whose first target failed and whose second was
    # cancelled
    first = await target_id(session_factory, seeded.session_id, 7)
    second = await add_target(
        session_factory,
        seeded.session_id,
        seeded.repository_id,
        number=8,
        status="cancelled",
    )
    await set_state(
        session_factory, seeded.session_id, status="cancelled", target_status="failed"
    )
    await set_target_state(session_factory, second, "cancelled")
    harness: ApiHarness = await build_harness(user_id=seeded.user_id)

    # When only the cancelled target is asked for
    response = await harness.client.post(
        f"/api/sessions/{seeded.session_id}/retry", json={"targetIds": [str(second)]}
    )

    # Then it is the cancelled one that goes back on the queue, alone
    assert response.status_code == 200
    assert response.json()["status"] == "queued"
    statuses = {
        target["id"]: target["status"] for target in response.json()["targets"]
    }
    assert statuses[str(second)] == "queued"
    assert statuses[str(first)] == "failed"
    assert harness.pool.jobs == [
        ("review_target", (str(seeded.session_id), str(second), "review"))
    ]


async def test_retry_refuses_a_target_the_queue_already_owns(
    seeded: Any, session_factory: Any, build_harness: Any
) -> None:
    # Given a running session whose target the queue owns
    running = await target_id(session_factory, seeded.session_id, 7)
    await set_state(session_factory, seeded.session_id, status="running", target_status="running")
    harness: ApiHarness = await build_harness(user_id=seeded.user_id)

    # When that target is asked for by hand
    response = await harness.client.post(
        f"/api/sessions/{seeded.session_id}/retry", json={"targetIds": [str(running)]}
    )

    # Then it is named in the refusal rather than duplicated onto the queue
    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "target_running"
    assert str(running) in error["detail"]
    assert harness.pool.jobs == []

    async with session_factory() as session:
        row = await session.get(SessionTarget, running)
        assert row is not None
        assert row.status == "running"


async def test_retry_recovers_a_queued_target_the_queue_lost(
    seeded: Any, session_factory: Any, build_harness: Any
) -> None:
    # Given a session whose only target has sat queued far longer than a job takes
    # to start it, with no attempt opened for it — what a job the queue refused
    # outright leaves behind, because it never touches the target
    target = await target_id(session_factory, seeded.session_id, 7)
    await set_state(
        session_factory, seeded.session_id, status="queued", target_status="queued"
    )
    await age_target(session_factory, target, age=_LOST_QUEUE_AGE)
    harness: ApiHarness = await build_harness(user_id=seeded.user_id)

    # When the session is retried
    response = await harness.client.post(f"/api/sessions/{seeded.session_id}/retry")

    # Then the target goes back on the queue instead of being refused: nothing else
    # can run it, and the response says so rather than reporting a conflict
    assert response.status_code == 200
    assert [item["status"] for item in response.json()["targets"]] == ["queued"]
    assert harness.pool.jobs == [
        ("review_target", (str(seeded.session_id), str(target), "review"))
    ]

    # And the recovery hands the queue back its claim on the target: an immediate
    # second retry is refused instead of enqueuing a second job for it
    again = await harness.client.post(
        f"/api/sessions/{seeded.session_id}/retry", json={"targetIds": [str(target)]}
    )
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "target_running"
    assert len(harness.pool.jobs) == 1


async def test_retry_recovers_a_lost_publish_as_a_publish_retry(
    seeded: Any, session_factory: Any, build_harness: Any
) -> None:
    # Given a queued target whose job is gone, and whose last attempt failed to
    # post a review it had already paid for
    target = await target_id(session_factory, seeded.session_id, 7)
    await set_state(
        session_factory, seeded.session_id, status="queued", target_status="queued"
    )
    await add_attempt(session_factory, target, error=_GITHUB_FAILURE, tokens=120)
    await age_target(session_factory, target, age=_LOST_QUEUE_AGE)
    harness: ApiHarness = await build_harness(user_id=seeded.user_id)

    # When the session is retried
    response = await harness.client.post(f"/api/sessions/{seeded.session_id}/retry")

    # Then the recovered job re-posts that review instead of buying it again, so
    # recovering the target does not silently turn a publish retry into a re-review
    assert response.status_code == 200
    assert harness.pool.jobs == [
        ("review_target", (str(seeded.session_id), str(target), "publish"))
    ]


async def test_retry_still_refuses_a_queued_target_that_is_only_fresh(
    seeded: Any, session_factory: Any, build_harness: Any
) -> None:
    # Given a session whose target was queued a moment ago, so its job is the
    # queue's to run, and which has no attempt of its own
    fresh = await target_id(session_factory, seeded.session_id, 7)
    await set_state(
        session_factory, seeded.session_id, status="queued", target_status="queued"
    )
    harness: ApiHarness = await build_harness(user_id=seeded.user_id)

    # When that target is retried
    response = await harness.client.post(
        f"/api/sessions/{seeded.session_id}/retry", json={"targetIds": [str(fresh)]}
    )

    # Then staleness is what decides it, and a fresh target is still refused rather
    # than duplicated onto the queue
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "target_running"
    assert harness.pool.jobs == []


async def test_retry_never_requeues_a_running_target_however_old(
    seeded: Any, session_factory: Any, build_harness: Any
) -> None:
    # Given a target that has been running for longer than the staleness threshold
    running = await target_id(session_factory, seeded.session_id, 7)
    await set_state(
        session_factory, seeded.session_id, status="running", target_status="running"
    )
    await age_target(session_factory, running, age=_LOST_QUEUE_AGE)
    harness: ApiHarness = await build_harness(user_id=seeded.user_id)

    # When that target is asked for by hand
    response = await harness.client.post(
        f"/api/sessions/{seeded.session_id}/retry", json={"targetIds": [str(running)]}
    )

    # Then age changes nothing: a worker is on it, and the staleness rule only ever
    # reads a queued target
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "target_running"
    assert harness.pool.jobs == []


async def test_retry_refuses_a_stale_queued_target_with_a_running_attempt(
    seeded: Any, session_factory: Any, build_harness: Any
) -> None:
    # Given a queued target old enough to be read as lost, but with an attempt that
    # is still running — a worker took the job, whatever the target's status says
    target = await target_id(session_factory, seeded.session_id, 7)
    await set_state(
        session_factory, seeded.session_id, status="running", target_status="queued"
    )
    await add_attempt(session_factory, target, status="running", tokens=0)
    await age_target(session_factory, target, age=_LOST_QUEUE_AGE)
    harness: ApiHarness = await build_harness(user_id=seeded.user_id)

    # When the session is retried
    response = await harness.client.post(
        f"/api/sessions/{seeded.session_id}/retry", json={"targetIds": [str(target)]}
    )

    # Then the running attempt is the evidence the queue did not lose the target,
    # so it is refused rather than duplicated
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "target_running"
    assert harness.pool.jobs == []


async def test_retry_with_nothing_retryable_conflicts(
    seeded: Any, session_factory: Any, build_harness: Any
) -> None:
    # Given a finished session whose only target succeeded
    done = await target_id(session_factory, seeded.session_id, 7)
    await set_state(session_factory, seeded.session_id, status="done", target_status="done")
    harness: ApiHarness = await build_harness(user_id=seeded.user_id)

    # When it is retried, with and without naming that target
    plain = await harness.client.post(f"/api/sessions/{seeded.session_id}/retry")
    named = await harness.client.post(
        f"/api/sessions/{seeded.session_id}/retry", json={"targetIds": [str(done)]}
    )

    # Then both report that there is nothing to retry, and nothing was enqueued
    assert plain.status_code == 409
    assert plain.json()["error"]["code"] == "nothing_to_retry"
    assert named.status_code == 409
    assert named.json()["error"]["code"] == "nothing_to_retry"
    assert harness.pool.jobs == []


async def test_retry_of_another_workspaces_session_is_404(
    seeded: Any, session_factory: Any, build_harness: Any
) -> None:
    # Given a failed session in a different workspace
    async with session_factory() as session:
        other_workspace, other_user = await seed_empty_workspace(session)
        await add_installation(
            session,
            other_workspace.id,
            installation_id=777,
            account_login="widgets",
            repositories=["widgets/app"],
        )
        other_repository = (
            await session.scalars(
                select(Repository).where(Repository.workspace_id == other_workspace.id)
            )
        ).one()
        other = await seed_session(
            session,
            workspace=other_workspace,
            user=other_user,
            repository=other_repository,
            status="failed",
        )
    harness: ApiHarness = await build_harness(user_id=seeded.user_id)

    # When it is retried from this workspace
    response = await harness.client.post(f"/api/sessions/{other.id}/retry")

    # Then it reads as absent, exactly like an unknown id
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "session_not_found"
    assert harness.pool.jobs == []


async def test_retry_for_a_viewer_without_repository_access_reads_as_absent(
    seeded: Any, session_factory: Any, build_harness: Any
) -> None:
    # Given a workspace member GitHub refuses the session's repository
    reader_id = await add_member(session_factory, seeded.workspace_id)
    await set_state(session_factory, seeded.session_id, status="failed", target_status="failed")
    harness: ApiHarness = await build_harness(
        user_id=reader_id, repo_access=make_checker(FakeRepoProbe(readable=False))
    )

    # When that viewer retries the session
    response = await harness.client.post(f"/api/sessions/{seeded.session_id}/retry")

    # Then the detail endpoint's 404 is what they get, and nothing is enqueued
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "session_not_found"
    assert harness.pool.jobs == []


async def test_retry_for_an_unverifiable_viewer_is_403(
    seeded: Any, session_factory: Any, build_harness: Any
) -> None:
    # Given a workspace member whose access cannot be checked at all
    reader_id = await add_member(session_factory, seeded.workspace_id)
    await set_state(session_factory, seeded.session_id, status="failed", target_status="failed")
    harness: ApiHarness = await build_harness(
        user_id=reader_id, repo_access=make_checker(FakeRepoProbe(unavailable=True))
    )

    # When that viewer retries the session
    response = await harness.client.post(f"/api/sessions/{seeded.session_id}/retry")

    # Then the check says so rather than quietly retrying less
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "repo_access_unverified"
    assert harness.pool.jobs == []


async def test_retry_rejects_a_target_from_another_session(
    seeded: Any, session_factory: Any, build_harness: Any
) -> None:
    # Given a second failed session in the same workspace
    async with session_factory() as session:
        workspace = await session.get(Workspace, seeded.workspace_id)
        user = await session.get(User, seeded.user_id)
        repository = await session.get(Repository, seeded.repository_id)
        assert workspace is not None and user is not None and repository is not None
        other = await seed_session(
            session,
            workspace=workspace,
            user=user,
            repository=repository,
            number=9,
            status="failed",
        )
    stranger = await target_id(session_factory, other.id, 9)
    first = await target_id(session_factory, seeded.session_id, 7)
    await set_state(session_factory, seeded.session_id, status="failed", target_status="failed")
    harness: ApiHarness = await build_harness(user_id=seeded.user_id)

    # When the other session's target is named against this one
    response = await harness.client.post(
        f"/api/sessions/{seeded.session_id}/retry", json={"targetIds": [str(stranger)]}
    )

    # Then it is refused as unknown here, and this session is left alone
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "target_not_found"
    assert harness.pool.jobs == []

    async with session_factory() as session:
        row = await session.get(SessionTarget, first)
        assert row is not None
        assert row.status == "failed"
