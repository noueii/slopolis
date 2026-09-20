"""Session detail, rename, cancel, and manual retry (spec 10.5 §Manual retry)."""

from __future__ import annotations

import uuid
from typing import Any

from app.services.repo_access import RepoAccessChecker, RepoAccessUnavailable
from sqlalchemy import select

from slopolis_db.models import (
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


class FakeRepoProbe:
    """Answers the viewer's repository read with a fixed verdict."""

    def __init__(self, *, readable: bool = True, unavailable: bool = False) -> None:
        self.readable = readable
        self.unavailable = unavailable
        self.calls: list[str] = []

    async def user_can_read(self, *, token: str, repo_full_name: str) -> bool:
        self.calls.append(repo_full_name)
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


async def add_attempt(session_factory: Any, target_id_: uuid.UUID) -> uuid.UUID:
    """Record a finished attempt for a target; return the run's id."""
    async with session_factory() as session:
        run = SessionTargetRun(
            target_id=target_id_,
            attempt=1,
            status="failed",
            error="provider timed out",
        )
        session.add(run)
        await session.commit()
        return run.id


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

    # And exactly one review_target job per target carries the submit path's args
    assert set(harness.pool.jobs) == {
        ("review_target", (str(seeded.session_id), str(first))),
        ("review_target", (str(seeded.session_id), str(second))),
    }

    # And the earlier attempt survives as history
    async with session_factory() as session:
        runs = (
            await session.scalars(
                select(SessionTargetRun).where(SessionTargetRun.target_id == first)
            )
        ).all()
    assert [run.id for run in runs] == [attempt]


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
        ("review_target", (str(seeded.session_id), str(second)))
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
