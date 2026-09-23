"""Connected repositories: the listing, its switches, and its access rule."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from app.routers import _pull_reads
from sqlalchemy import select

from slopolis_core.github.errors import GitHubError
from slopolis_core.github.models import GitHubPullRequest
from slopolis_db.models import AuditLog, Repository

from .conftest import (
    ApiHarness,
    FakeGitHubClient,
    FakeInstallationClients,
    add_installation,
    seed_empty_workspace,
)


def _pull(repo: str, number: int, title: str) -> GitHubPullRequest:
    """Build a typed GitHub pull request for the fake client."""
    return GitHubPullRequest(
        repo_full_name=repo,
        private=False,
        number=number,
        title=title,
        url=f"https://github.com/{repo}/pull/{number}",
        head_branch="fix/branch",
        base_branch="main",
        head_sha="abc123",
        default_branch="main",
        body="",
        author_login="octocat",
        draft=False,
        changed_files=3,
        additions=40,
        deletions=7,
        updated_at="2026-01-01T00:00:00Z",
    )


class CountingGitHubClient(FakeGitHubClient):
    """Fake GitHub client that records every read and names its installation.

    The installation id is what the router keys its cache by, so this stands in
    for the real per-installation client. ``failing`` holds repositories GitHub
    refuses to answer for.
    """

    def __init__(
        self, *args: Any, installation_id: int = 555, **kwargs: Any
    ) -> None:
        super().__init__(*args, **kwargs)
        self.installation_id = installation_id
        self.calls: list[str] = []
        self.failing: set[str] = set()

    async def list_open_pull_requests(self, full_name: str) -> list[Any]:
        self.calls.append(f"list_open_pull_requests:{full_name}")
        if full_name in self.failing:
            raise GitHubError(f"{full_name} is unavailable")
        return await super().list_open_pull_requests(full_name)

    async def get_pull_request(self, full_name: str, number: int) -> Any:
        self.calls.append(f"get_pull_request:{full_name}#{number}")
        return await super().get_pull_request(full_name, number)

    async def list_check_runs(self, full_name: str, ref: str) -> list[Any]:
        self.calls.append(f"list_check_runs:{full_name}@{ref}")
        return await super().list_check_runs(full_name, ref)


async def test_list_repositories_reports_open_pull_counts(
    seeded: Any, build_harness: Any
) -> None:
    # Given a seeded workspace with one connected repository holding one open PR
    client = FakeGitHubClient({"acme/api": [_pull("acme/api", 42, "Add guard")]})
    harness: ApiHarness = await build_harness(
        user_id=seeded.user_id, github_client=client
    )

    # When repositories are listed
    response = await harness.client.get("/api/repositories")

    # Then the summary is returned in camelCase with the live open count
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 1
    assert items[0]["fullName"] == "acme/api"
    assert items[0]["defaultBranch"] == "main"
    assert items[0]["connected"] is True
    assert items[0]["openPrCount"] == 1


async def test_list_repositories_re_reads_once_the_cache_expires(
    seeded: Any, build_harness: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given a cache TTL short enough to watch expire
    monkeypatch.setattr(_pull_reads, "PULL_CACHE_TTL_SECONDS", 0.2)
    client = CountingGitHubClient({"acme/api": [_pull("acme/api", 42, "Add guard")]})
    harness: ApiHarness = await build_harness(
        user_id=seeded.user_id, github_client=client
    )
    first = await harness.client.get("/api/repositories")
    await asyncio.sleep(0.25)

    # When the picker is mounted again after the TTL
    second = await harness.client.get("/api/repositories")

    # Then GitHub is read again, with the same answer as before
    assert client.calls == [
        "list_open_pull_requests:acme/api",
        "list_open_pull_requests:acme/api",
    ]
    assert second.json() == first.json()


async def test_a_repository_github_refuses_is_not_cached_as_empty(
    seeded: Any, build_harness: Any
) -> None:
    # Given a repository the App cannot read
    client = CountingGitHubClient({"acme/api": [_pull("acme/api", 42, "Add guard")]})
    client.failing.add("acme/api")
    harness: ApiHarness = await build_harness(
        user_id=seeded.user_id, github_client=client
    )

    # When the picker is mounted twice
    first = await harness.client.get("/api/repositories")
    second = await harness.client.get("/api/repositories")

    # Then the count falls back to 0 both times instead of freezing a bad read
    assert first.json()["items"][0]["openPrCount"] == 0
    assert second.json() == first.json()
    assert client.calls == [
        "list_open_pull_requests:acme/api",
        "list_open_pull_requests:acme/api",
    ]


async def test_list_repositories_reads_each_installation_with_its_own_client(
    seeded: Any, session_factory: Any, build_harness: Any
) -> None:
    # Given a workspace holding two installations, each with a repository
    async with session_factory() as session:
        await add_installation(
            session,
            seeded.workspace_id,
            installation_id=777,
            account_login="widgets",
            repositories=["widgets/app"],
        )
    acme = CountingGitHubClient(
        {"acme/api": [_pull("acme/api", 42, "Add guard")]}, installation_id=555
    )
    widgets = CountingGitHubClient(
        {"widgets/app": [_pull("widgets/app", 7, "Add widget")]}, installation_id=777
    )
    registry = FakeInstallationClients({555: acme, 777: widgets})
    harness: ApiHarness = await build_harness(
        user_id=seeded.user_id, github_clients=registry
    )

    # When the picker is mounted
    response = await harness.client.get("/api/repositories")

    # Then both installations' repositories are listed, in name order...
    assert response.status_code == 200
    items = response.json()["items"]
    assert [item["fullName"] for item in items] == ["acme/api", "widgets/app"]
    assert [item["openPrCount"] for item in items] == [1, 1]
    # ...each read through its own client...
    assert acme.calls == ["list_open_pull_requests:acme/api"]
    assert widgets.calls == ["list_open_pull_requests:widgets/app"]
    # ...and each installation minted once, for this request
    assert registry.minted == [555, 777]


async def test_a_repository_whose_installation_cannot_mint_still_lists(
    seeded: Any, session_factory: Any, build_harness: Any
) -> None:
    # Given a second installation the App credentials cannot mint a token for
    async with session_factory() as session:
        await add_installation(
            session,
            seeded.workspace_id,
            installation_id=777,
            account_login="widgets",
            repositories=["widgets/app"],
        )
    acme = CountingGitHubClient(
        {"acme/api": [_pull("acme/api", 42, "Add guard")]}, installation_id=555
    )
    registry = FakeInstallationClients({555: acme})
    harness: ApiHarness = await build_harness(
        user_id=seeded.user_id, github_clients=registry
    )

    # When the picker is mounted twice
    first = await harness.client.get("/api/repositories")
    second = await harness.client.get("/api/repositories")

    # Then the readable installation is counted, the other degrades to no count...
    counts = {
        item["fullName"]: item["openPrCount"] for item in first.json()["items"]
    }
    assert counts == {"acme/api": 1, "widgets/app": 0}
    assert second.json() == first.json()
    # ...its failure was never cached as a zero read (it is retried, the readable
    # installation's count is served from the cache)...
    assert acme.calls == ["list_open_pull_requests:acme/api"]
    assert registry.minted == [555, 777, 555, 777]


async def test_a_workspace_with_no_installation_lists_rows_without_live_counts(
    seeded: Any, build_harness: Any
) -> None:
    # Given a workspace whose App credentials are unconfigured (no registry)
    harness: ApiHarness = await build_harness(user_id=seeded.user_id)

    # When the picker is mounted
    response = await harness.client.get("/api/repositories")

    # Then the DB rows still list, with no live count
    assert response.status_code == 200
    items = response.json()["items"]
    assert [item["fullName"] for item in items] == ["acme/api"]
    assert items[0]["openPrCount"] == 0


async def test_an_installation_added_while_the_process_runs_is_read(
    session_factory: Any, build_harness: Any
) -> None:
    # Given a signed-in workspace with nothing connected yet
    async with session_factory() as session:
        workspace, user = await seed_empty_workspace(session)
    rookie = CountingGitHubClient(
        {"rookie/tool": [_pull("rookie/tool", 3, "Add tool")]}, installation_id=888
    )
    registry = FakeInstallationClients({888: rookie})
    harness: ApiHarness = await build_harness(
        user_id=user.id, github_clients=registry
    )
    before = await harness.client.get("/api/repositories")
    assert before.json()["items"] == []

    # When the installation is recorded while the process runs — the setup
    # callback, or a webhook — with no restart in between
    async with session_factory() as session:
        await add_installation(
            session,
            workspace.id,
            installation_id=888,
            account_login="rookie",
            repositories=["rookie/tool"],
        )
    after = await harness.client.get("/api/repositories")

    # Then the same request sees it, read live through its installation's client
    items = after.json()["items"]
    assert [item["fullName"] for item in items] == ["rookie/tool"]
    assert items[0]["openPrCount"] == 1
    assert rookie.calls == ["list_open_pull_requests:rookie/tool"]


class InFlight:
    """Tracks how many reads were in flight at once."""

    def __init__(self) -> None:
        self.current = 0
        self.peak = 0

    def enter(self) -> None:
        self.current += 1
        self.peak = max(self.peak, self.current)

    def exit(self) -> None:
        self.current -= 1


class SlowGitHubClient(CountingGitHubClient):
    """Counting client that holds each read open long enough to overlap."""

    def __init__(self, *args: Any, tracker: InFlight, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._tracker = tracker

    async def list_open_pull_requests(self, full_name: str) -> list[Any]:
        self._tracker.enter()
        try:
            await asyncio.sleep(0.01)
            return await super().list_open_pull_requests(full_name)
        finally:
            self._tracker.exit()


async def test_reads_stay_bounded_across_installations(
    seeded: Any, session_factory: Any, build_harness: Any
) -> None:
    # Given a workspace whose two installations hold nine repositories between them
    widgets = [f"widgets/app{index}" for index in range(8)]
    async with session_factory() as session:
        await add_installation(
            session,
            seeded.workspace_id,
            installation_id=777,
            account_login="widgets",
            repositories=widgets,
        )
    tracker = InFlight()
    acme = SlowGitHubClient(
        {"acme/api": [_pull("acme/api", 42, "Add guard")]},
        installation_id=555,
        tracker=tracker,
    )
    other = SlowGitHubClient(installation_id=777, tracker=tracker)
    harness: ApiHarness = await build_harness(
        user_id=seeded.user_id,
        github_clients=FakeInstallationClients({555: acme, 777: other}),
    )

    # When the picker is mounted
    response = await harness.client.get("/api/repositories")

    # Then every repository was read, never more than the bound at once —
    # installations share one budget instead of multiplying it
    assert len(response.json()["items"]) == 9
    assert tracker.peak == _pull_reads.MAX_CONCURRENT_READS


async def test_parking_a_repository_keeps_it_listed_and_audited(
    seeded: Any, session_factory: Any, build_harness: Any
) -> None:
    # Given a connected repository the workspace reviews
    harness: ApiHarness = await build_harness(user_id=seeded.user_id)
    listed = await harness.client.get("/api/repositories")
    assert listed.json()["items"][0]["enabled"] is True

    # When it is parked
    response = await harness.client.patch(
        f"/api/repositories/{seeded.repository_id}", json={"enabled": False}
    )

    # Then the toggle answers with the updated summary...
    assert response.status_code == 200
    body = response.json()
    assert body["fullName"] == "acme/api"
    assert body["enabled"] is False
    # ...the repository still lists, with its history, marked as parked...
    after = await harness.client.get("/api/repositories")
    assert [item["fullName"] for item in after.json()["items"]] == ["acme/api"]
    assert after.json()["items"][0]["enabled"] is False
    # ...and the change is recorded against the actor
    async with session_factory() as session:
        row = await session.get(Repository, seeded.repository_id)
        assert row is not None and row.enabled is False
        audits = list((await session.scalars(select(AuditLog))).all())
    assert [(audit.action, audit.actor_user_id) for audit in audits] == [
        ("repository.disabled", seeded.user_id)
    ]
    assert audits[0].target_id == seeded.repository_id

    # When it is re-enabled
    enabled = await harness.client.patch(
        f"/api/repositories/{seeded.repository_id}", json={"enabled": True}
    )

    # Then it is reviewed again and the transition is audited too
    assert enabled.json()["enabled"] is True
    async with session_factory() as session:
        actions = [
            audit.action for audit in (await session.scalars(select(AuditLog))).all()
        ]
    assert actions == ["repository.disabled", "repository.enabled"]


async def test_parking_an_already_parked_repository_changes_nothing(
    seeded: Any, session_factory: Any, build_harness: Any
) -> None:
    # Given a repository already parked
    harness: ApiHarness = await build_harness(user_id=seeded.user_id)
    first = await harness.client.patch(
        f"/api/repositories/{seeded.repository_id}", json={"enabled": False}
    )

    # When the same toggle is replayed
    second = await harness.client.patch(
        f"/api/repositories/{seeded.repository_id}", json={"enabled": False}
    )

    # Then the answer is the same and no second audit row was written
    assert second.status_code == 200
    assert second.json() == first.json()
    async with session_factory() as session:
        actions = [
            audit.action for audit in (await session.scalars(select(AuditLog))).all()
        ]
    assert actions == ["repository.disabled"]


async def test_parking_another_workspaces_repository_is_404(
    seeded: Any, session_factory: Any, build_harness: Any
) -> None:
    # Given a repository belonging to a different workspace
    async with session_factory() as session:
        other, _user = await seed_empty_workspace(session)
        await add_installation(
            session,
            other.id,
            installation_id=999,
            account_login="other",
            repositories=["other/repo"],
        )
        elsewhere = await session.scalar(
            select(Repository).where(Repository.workspace_id == other.id)
        )
    assert elsewhere is not None
    harness: ApiHarness = await build_harness(user_id=seeded.user_id)

    # When this workspace tries to park it
    response = await harness.client.patch(
        f"/api/repositories/{elsewhere.id}", json={"enabled": False}
    )

    # Then it is not found, and its own workspace's switch is untouched
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "repository_not_found"
    async with session_factory() as session:
        row = await session.get(Repository, elsewhere.id)
        assert row is not None and row.enabled is True
        assert await session.scalar(select(AuditLog).limit(1)) is None


# --- the update body --------------------------------------------------------


async def test_a_body_without_the_switch_is_refused(
    seeded: Any, session_factory: Any, build_harness: Any
) -> None:
    # Given a connected repository
    harness: ApiHarness = await build_harness(user_id=seeded.user_id)

    # When a body omits ``enabled``, which leaves the endpoint nothing to do
    response = await harness.client.patch(
        f"/api/repositories/{seeded.repository_id}", json={}
    )

    # Then it is a validation failure, and the row stays exactly as it was,
    # with nothing audited
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    async with session_factory() as session:
        row = await session.get(Repository, seeded.repository_id)
        assert row is not None and row.enabled is True
        assert await session.scalar(select(AuditLog).limit(1)) is None
