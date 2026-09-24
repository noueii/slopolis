"""Per-viewer repository access (spec 10.8 §Access).

Two layers are covered here: the checker itself — three-way verdicts, the TTL
cache, token resolution — and the read paths that consume it, which must narrow
the session list, the pull-request inbox, and the session detail to the
repositories the *viewer* can read rather than the ones the installation can.
"""

from __future__ import annotations

import base64
import uuid
from collections.abc import Iterator, Mapping
from typing import Any

import httpx
import pytest
import respx
from app.deps import get_vault, user_github_token
from app.services.repo_access import (
    GitHubRepoProbe,
    RepoAccessChecker,
    RepoAccessUnavailable,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from slopolis_core.cache import TTLCache
from slopolis_core.github.models import GitHubPullRequest
from slopolis_core.settings import get_settings
from slopolis_db.models import (
    Finding,
    Repository,
    SessionTarget,
    User,
    Workspace,
)

from .conftest import ApiHarness, FakeGitHubClient, add_installation, seed_session

_API_PATH = "/repos/acme/api"
_HIDDEN_PATH = "/repos/acme/hidden"
_API_URL = f"https://api.github.com{_API_PATH}"
_MASTER_KEY = base64.b64encode(b"repo-access-tests-master-key-material").decode()
_ROTATED_KEY = base64.b64encode(b"a-rotated-master-key-material-enough").decode()


def inbox_client(*full_names: str) -> FakeGitHubClient:
    """A GitHub fake listing one open pull request per named repository.

    The inbox reads GitHub for the rows it filters, so the tests that use it as a
    read path need a listing to filter: the repository access rule is what decides
    whether those rows come back.
    """
    return FakeGitHubClient(
        {
            full_name: [
                GitHubPullRequest(
                    repo_full_name=full_name,
                    private=False,
                    number=7,
                    title=f"fix: {full_name} change",
                    url=f"https://github.com/{full_name}/pull/7",
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
            ]
            for full_name in full_names
        }
    )


# --- fixtures and doubles ---------------------------------------------------


class FakeRepoProbe:
    """Answers from a fixed map and records every check it was asked to make."""

    def __init__(
        self,
        verdicts: Mapping[str, bool] | None = None,
        *,
        unavailable: bool = False,
    ) -> None:
        self.verdicts = dict(verdicts or {})
        self.unavailable = unavailable
        self.calls: list[tuple[str, str]] = []

    async def user_can_read(self, *, token: str, repo_full_name: str) -> bool:
        self.calls.append((token, repo_full_name))
        if self.unavailable:
            raise RepoAccessUnavailable("github is unreachable")
        return self.verdicts.get(repo_full_name, False)


class FakeClock:
    """A monotonic clock a test can move, for the cache's TTL."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def make_user(*, user_id: uuid.UUID | None = None) -> User:
    """Build a detached user row, without a database."""
    return User(
        id=user_id or uuid.uuid4(),
        workspace_id=None,
        github_id=77,
        handle="reader",
        name="Reader",
        avatar_url=None,
    )


def make_checker(
    probe: FakeRepoProbe,
    *,
    token: str | None = "gho_reader",
    cache: TTLCache[tuple[uuid.UUID, str], bool] | None = None,
) -> RepoAccessChecker:
    """Checker over ``probe``, with a token source standing in for the vault."""
    return RepoAccessChecker(probe=probe, tokens=lambda _user: token, cache=cache)


async def add_member(
    session_factory: Any,
    workspace_id: uuid.UUID,
    *,
    handle: str = "reader",
    github_id: int = 1002,
) -> uuid.UUID:
    """Add an ordinary workspace member who triggered nothing; return its id."""
    async with session_factory() as session:
        user = User(
            workspace_id=workspace_id,
            github_id=github_id,
            handle=handle,
            name=handle.title(),
            avatar_url=None,
        )
        session.add(user)
        await session.commit()
        return user.id


async def seed_repo(
    session: AsyncSession, seeded: Any, *, installation_id: int, full_name: str
) -> Repository:
    """Connect one more repository to the seeded workspace."""
    await add_installation(
        session,
        seeded.workspace_id,
        installation_id=installation_id,
        account_login="acme",
        repositories=[full_name],
    )
    repository = await session.scalar(
        select(Repository).where(Repository.full_name == full_name)
    )
    assert repository is not None
    return repository


async def add_hidden_target(session_factory: Any, seeded: Any, *, number: int = 9) -> None:
    """Give the seeded session a second target in acme/hidden, with one finding."""
    async with session_factory() as session:
        repository = await seed_repo(
            session, seeded, installation_id=888, full_name="acme/hidden"
        )
        target = SessionTarget(
            session_id=seeded.session_id,
            repository_id=repository.id,
            number=number,
            title="fix: hidden change",
            url=f"https://github.com/acme/hidden/pull/{number}",
            head_branch="fix/hidden",
            status="done",
            tokens=999,
            cost_usd=0,
        )
        session.add(target)
        await session.flush()
        session.add(
            Finding(
                target_id=target.id,
                path="src/secret.py",
                line=1,
                severity="error",
                category="security",
                message="must not leak",
                confidence=0.9,
            )
        )
        await session.commit()


async def seed_hidden_sessions(session_factory: Any, seeded: Any, *, count: int = 2) -> None:
    """Add ``count`` more sessions, triggered by octocat, in acme/hidden."""
    async with session_factory() as session:
        workspace = await session.get(Workspace, seeded.workspace_id)
        user = await session.get(User, seeded.user_id)
        assert workspace is not None and user is not None
        repository = await seed_repo(
            session, seeded, installation_id=777, full_name="acme/hidden"
        )
        for number in range(1, count + 1):
            await seed_session(
                session,
                workspace=workspace,
                user=user,
                repository=repository,
                number=number,
                status="done",
                title=f"Hidden review {number}",
            )


# --- the checker ------------------------------------------------------------


async def test_verdicts_are_true_false_and_unverifiable() -> None:
    # Given a probe that grants one repository, refuses another, and cannot
    # answer about a third
    probe = FakeRepoProbe({"acme/api": True, "acme/secret": False})
    checker = make_checker(probe)
    user = make_user()

    # When each is checked
    granted = await checker.can_read(user, "acme/api")
    refused = await checker.can_read(user, "acme/secret")
    probe.unavailable = True
    unreachable = await checker.can_read(user, "acme/other")

    # Then the three outcomes stay distinct
    assert granted is True
    assert refused is False
    assert unreachable is None


async def test_no_stored_token_is_unverifiable_without_calling_github() -> None:
    # Given a viewer whose account holds no usable token
    probe = FakeRepoProbe({"acme/api": True})
    checker = make_checker(probe, token=None)

    # When access is checked
    verdict = await checker.can_read(make_user(), "acme/api")

    # Then it is unverifiable, and GitHub was never asked
    assert verdict is None
    assert probe.calls == []


async def test_batch_checks_each_repository_once_with_the_users_token() -> None:
    # Given three names, one of them repeated
    probe = FakeRepoProbe({"acme/api": True, "acme/other": False})
    checker = make_checker(probe, token="gho_reader")

    # When they are checked in one batch
    verdicts = await checker.can_read_many(
        make_user(), ["acme/api", "acme/other", "acme/api"]
    )

    # Then each distinct repository cost exactly one call, under the user's token
    assert verdicts == {"acme/api": True, "acme/other": False}
    assert probe.calls == [("gho_reader", "acme/api"), ("gho_reader", "acme/other")]


async def test_a_cached_verdict_is_not_rechecked_until_the_ttl_expires() -> None:
    # Given a checker with a clock we control
    clock = FakeClock()
    probe = FakeRepoProbe({"acme/api": True})
    checker = make_checker(
        probe, cache=TTLCache(ttl_seconds=60.0, clock=clock)
    )
    user = make_user()

    # When the same repository is checked twice inside the window
    assert await checker.can_read(user, "acme/api") is True
    assert await checker.can_read(user, "acme/api") is True
    assert len(probe.calls) == 1

    # And again after the window
    clock.advance(61.0)
    assert await checker.can_read(user, "acme/api") is True
    assert len(probe.calls) == 2


async def test_an_unverifiable_verdict_is_never_cached() -> None:
    # Given GitHub being unreachable
    clock = FakeClock()
    probe = FakeRepoProbe(unavailable=True)
    checker = make_checker(
        probe, cache=TTLCache(ttl_seconds=60.0, clock=clock)
    )
    user = make_user()

    # When the same repository is checked twice
    assert await checker.can_read(user, "acme/api") is None
    assert await checker.can_read(user, "acme/api") is None

    # Then the outage was not remembered for the whole window
    assert len(probe.calls) == 2


# --- the GitHub probe -------------------------------------------------------


@respx.mock
async def test_probe_maps_github_statuses_onto_verdicts() -> None:
    # Given the repository endpoint GitHub answers per status
    route = respx.get(_API_URL)
    probe = GitHubRepoProbe()

    # 200 is readable, 404 (hidden private) and 403 (public, no access) are not
    route.mock(return_value=httpx.Response(200, json={"full_name": "acme/api"}))
    assert await probe.user_can_read(token="gho_x", repo_full_name="acme/api") is True
    route.mock(return_value=httpx.Response(404, json={}))
    assert await probe.user_can_read(token="gho_x", repo_full_name="acme/api") is False
    route.mock(return_value=httpx.Response(403, json={}))
    assert await probe.user_can_read(token="gho_x", repo_full_name="acme/api") is False

    # And it carried the user's own token
    assert route.calls.last.request.headers["authorization"] == "Bearer gho_x"

    # A revoked token, a rate limit, a server error, and a dead network all mean
    # the check could not be made
    route.mock(return_value=httpx.Response(401, json={}))
    with pytest.raises(RepoAccessUnavailable):
        await probe.user_can_read(token="gho_x", repo_full_name="acme/api")
    route.mock(
        return_value=httpx.Response(403, headers={"x-ratelimit-remaining": "0"}, json={})
    )
    with pytest.raises(RepoAccessUnavailable):
        await probe.user_can_read(token="gho_x", repo_full_name="acme/api")
    route.mock(return_value=httpx.Response(500, text="boom"))
    with pytest.raises(RepoAccessUnavailable):
        await probe.user_can_read(token="gho_x", repo_full_name="acme/api")
    route.mock(side_effect=httpx.ConnectError("no route to host"))
    with pytest.raises(RepoAccessUnavailable):
        await probe.user_can_read(token="gho_x", repo_full_name="acme/api")


# --- token resolution -------------------------------------------------------


@pytest.fixture
def vault_ready(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Give the process a vault key and drop the settings/vault memos."""
    monkeypatch.setenv("ENCRYPTION_KEY", _MASTER_KEY)
    _reset_memos()
    yield
    _reset_memos()


def _reset_memos() -> None:
    """Clear the cached settings and vault after an environment change."""
    get_settings.cache_clear()
    get_vault.cache_clear()


async def test_the_stored_token_opens_for_its_user(vault_ready: None) -> None:
    # Given an account whose token was sealed at sign-in
    user = make_user()
    user.encrypted_github_token = get_vault().seal("gho_signed_in")

    # Then the check runs with that token
    assert user_github_token(user) == "gho_signed_in"


async def test_a_rotated_master_key_is_unverifiable_not_an_error(
    vault_ready: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given a token sealed under the previous ENCRYPTION_KEY
    user = make_user()
    user.encrypted_github_token = get_vault().seal("gho_signed_in")
    monkeypatch.setenv("ENCRYPTION_KEY", _ROTATED_KEY)
    _reset_memos()

    # Then it cannot be used, and that is unverifiable rather than a failure
    assert user_github_token(user) is None


async def test_without_a_vault_no_token_is_usable(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given a deployment that stores no token (no ENCRYPTION_KEY)
    monkeypatch.setenv("ENCRYPTION_KEY", "")
    _reset_memos()
    user = make_user()
    user.encrypted_github_token = b"\x01sealed-under-another-key"

    # Then an account with a blob, and one with nothing, both read as unusable
    assert user_github_token(user) is None
    assert user_github_token(make_user()) is None


# --- read filtering ---------------------------------------------------------


async def test_the_triggerer_sees_their_session_without_a_github_call(
    seeded: Any, build_harness: Any
) -> None:
    # Given a session the viewer themselves submitted
    probe = FakeRepoProbe(unavailable=True)
    harness: ApiHarness = await build_harness(
        user_id=seeded.user_id, repo_access=make_checker(probe)
    )

    # When the list and the detail are read
    listing = await harness.client.get("/api/sessions")
    detail = await harness.client.get(f"/api/sessions/{seeded.session_id}")

    # Then the session is fully visible and no check was spent on it
    assert listing.json()["total"] == 1
    assert detail.status_code == 200
    assert detail.json()["targets"][0]["repository"]["fullName"] == "acme/api"
    assert probe.calls == []


async def test_a_member_with_access_sees_the_session_everywhere(
    seeded: Any, session_factory: Any, build_harness: Any
) -> None:
    # Given a workspace member who may read the repository they did not trigger
    reader_id = await add_member(session_factory, seeded.workspace_id)
    probe = FakeRepoProbe({"acme/api": True})
    harness: ApiHarness = await build_harness(
        user_id=reader_id,
        repo_access=make_checker(probe),
        github_client=inbox_client("acme/api"),
    )

    # When the list, the inbox, and the detail are read
    listing = await harness.client.get("/api/sessions")
    inbox = await harness.client.get("/api/pull-requests")
    detail = await harness.client.get(f"/api/sessions/{seeded.session_id}")

    # Then the session and its target are visible on all three
    assert listing.json()["total"] == 1
    assert listing.json()["items"][0]["targets"][0]["repository"]["fullName"] == "acme/api"
    assert inbox.json()["total"] == 1
    assert inbox.json()["items"][0]["review"]["sessionId"] == str(seeded.session_id)
    assert detail.status_code == 200
    assert detail.json()["targetCount"] == 1


async def test_a_member_without_access_sees_nothing(
    seeded: Any, session_factory: Any, build_harness: Any
) -> None:
    # Given a member GitHub refuses to grant the repository
    reader_id = await add_member(session_factory, seeded.workspace_id)
    probe = FakeRepoProbe({"acme/api": False})
    harness: ApiHarness = await build_harness(
        user_id=reader_id,
        repo_access=make_checker(probe),
        github_client=inbox_client("acme/api"),
    )

    # When they read the list, the inbox, and the detail
    listing = await harness.client.get("/api/sessions")
    inbox = await harness.client.get("/api/pull-requests")
    detail = await harness.client.get(f"/api/sessions/{seeded.session_id}")

    # Then the session is hidden, the inbox has no row for it, and the detail
    # reads as absent
    assert listing.json()["total"] == 0
    assert listing.json()["items"] == []
    assert inbox.json()["total"] == 0
    assert inbox.json()["items"] == []
    assert inbox.json()["summary"]["total"] == 0
    assert detail.status_code == 404
    assert detail.json()["error"]["code"] == "session_not_found"
    assert probe.calls == [("gho_reader", "acme/api")]


@pytest.mark.parametrize("cause", ["no_token", "unreachable"])
async def test_an_unverifiable_viewer_gets_403_on_the_detail_but_no_list_row(
    seeded: Any, session_factory: Any, build_harness: Any, cause: str
) -> None:
    # Given a member whose access cannot be established — they hold no token, or
    # GitHub cannot be reached
    reader_id = await add_member(session_factory, seeded.workspace_id)
    probe = FakeRepoProbe(unavailable=cause == "unreachable")
    checker = make_checker(probe, token=None if cause == "no_token" else "gho_reader")
    harness: ApiHarness = await build_harness(user_id=reader_id, repo_access=checker)

    # When they read the detail and the list
    detail = await harness.client.get(f"/api/sessions/{seeded.session_id}")
    listing = await harness.client.get("/api/sessions")

    # Then the detail says so rather than quietly showing less, and the list
    # simply does not include the session
    assert detail.status_code == 403
    assert detail.json()["error"]["code"] == "repo_access_unverified"
    assert listing.json()["total"] == 0
    assert listing.json()["items"] == []


async def test_detail_shows_only_the_readable_targets_and_their_findings(
    seeded: Any, session_factory: Any, build_harness: Any
) -> None:
    # Given a two-target session, one target in a repository the viewer may read
    # and one (with a finding) in a repository they may not
    await add_hidden_target(session_factory, seeded)
    reader_id = await add_member(session_factory, seeded.workspace_id)
    probe = FakeRepoProbe({"acme/api": True, "acme/hidden": False})
    harness: ApiHarness = await build_harness(
        user_id=reader_id, repo_access=make_checker(probe)
    )

    # When the session is read
    detail = await harness.client.get(f"/api/sessions/{seeded.session_id}")

    # Then only the readable target, and none of the hidden finding, comes back
    assert detail.status_code == 200
    body = detail.json()
    assert body["targetCount"] == 1
    assert body["findingsCount"] == 0
    assert body["tokens"] == 120
    assert [target["repository"]["fullName"] for target in body["targets"]] == ["acme/api"]


async def test_list_totals_describe_the_filtered_set_not_the_page(
    seeded: Any, session_factory: Any, build_harness: Any
) -> None:
    # Given three sessions, only one of them in a repository the viewer can read
    await seed_hidden_sessions(session_factory, seeded, count=2)
    reader_id = await add_member(session_factory, seeded.workspace_id)
    probe = FakeRepoProbe({"acme/api": True, "acme/hidden": False})
    harness: ApiHarness = await build_harness(
        user_id=reader_id, repo_access=make_checker(probe)
    )

    # When a single-row page is requested
    response = await harness.client.get("/api/sessions", params={"pageSize": 1})

    # Then the counters describe the filtered set, not the unfiltered workspace
    body = response.json()
    assert body["total"] == 1
    assert body["totalPages"] == 1
    assert [item["id"] for item in body["items"]] == [str(seeded.session_id)]


async def test_a_list_costs_one_check_per_repository_not_per_session(
    seeded: Any, session_factory: Any, build_harness: Any
) -> None:
    # Given three sessions spread over two repositories
    await seed_hidden_sessions(session_factory, seeded, count=2)
    reader_id = await add_member(session_factory, seeded.workspace_id)
    probe = FakeRepoProbe({"acme/api": True, "acme/hidden": True})
    harness: ApiHarness = await build_harness(
        user_id=reader_id, repo_access=make_checker(probe)
    )

    # When the list is read twice
    first = await harness.client.get("/api/sessions")
    second = await harness.client.get("/api/sessions")

    # Then each repository cost one check, and the second read was served from
    # the cache entirely
    assert first.json()["total"] == 3
    assert second.json()["total"] == 3
    assert sorted(name for _token, name in probe.calls) == ["acme/api", "acme/hidden"]


async def test_free_text_filter_cannot_match_hidden_content(
    seeded: Any, session_factory: Any, build_harness: Any
) -> None:
    # Given a session whose second target — hidden from the viewer — is the only
    # place the word "hidden" appears
    await add_hidden_target(session_factory, seeded)
    reader_id = await add_member(session_factory, seeded.workspace_id)
    probe = FakeRepoProbe({"acme/api": True, "acme/hidden": False})
    harness: ApiHarness = await build_harness(
        user_id=reader_id, repo_access=make_checker(probe)
    )

    # When the viewer searches for that word, and for one they may read
    hidden = await harness.client.get("/api/sessions", params={"q": "hidden"})
    readable = await harness.client.get("/api/sessions", params={"q": "token"})

    # Then the hidden target is not searchable, while session-level fields still are
    assert hidden.json()["total"] == 0
    assert readable.json()["total"] == 1


async def test_filtering_by_a_repository_does_not_grant_access_to_it(
    seeded: Any, session_factory: Any, build_harness: Any
) -> None:
    # Given sessions in a repository the viewer may not read, named explicitly
    await seed_hidden_sessions(session_factory, seeded, count=1)
    reader_id = await add_member(session_factory, seeded.workspace_id)
    probe = FakeRepoProbe({"acme/api": True, "acme/hidden": False})
    harness: ApiHarness = await build_harness(
        user_id=reader_id,
        repo_access=make_checker(probe),
        github_client=inbox_client("acme/api", "acme/hidden"),
    )

    # When the list and the inbox are scoped to that repository
    listing = await harness.client.get("/api/sessions", params={"repo": "acme/hidden"})
    inbox = await harness.client.get("/api/pull-requests", params={"repo": "acme/hidden"})

    # Then asking for it by name still returns nothing
    assert listing.json()["total"] == 0
    assert listing.json()["items"] == []
    assert inbox.json()["total"] == 0
    assert inbox.json()["items"] == []
