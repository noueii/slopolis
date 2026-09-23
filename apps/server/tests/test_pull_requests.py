"""The pull-request inbox (spec v3 §1-§3, §6).

Every case here is asserted over HTTP: what a row reports about its review, what
the filters select, what the header counts, and what happens when GitHub refuses
part of the workspace. The GitHub reads are the shared in-memory fake, so no test
touches the network, and the access checks run over an in-memory probe.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import pytest
import pytest_asyncio
from app.services.repo_access import RepoAccessChecker

from slopolis_core.github.errors import GitHubError
from slopolis_core.github.models import CheckRun, GitHubPullRequest
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
    FakeGitHubClient,
    FakeInstallationClients,
    add_installation,
    seed_empty_workspace,
    seed_workspace,
)

_REPO = "acme/api"
_HEAD = "headsha"

#: A second commit graph for the staleness cases: the review covered ``oldsha``
#: and the pull request head has since moved to ``headsha``.
_REVIEWED = "oldsha"


@dataclass(frozen=True)
class Inbox:
    """The workspace the inbox tests read: one member and one connected repository."""

    workspace_id: uuid.UUID
    user_id: uuid.UUID
    repository_id: uuid.UUID


class Probe:
    """Repository visibility, without GitHub or a vault.

    Answers ``default`` for a repository it was not told about, so a test that
    does not care about the access rule grants everything by default.
    """

    def __init__(
        self, verdicts: Mapping[str, bool] | None = None, *, default: bool = True
    ) -> None:
        self.verdicts = dict(verdicts or {})
        self.default = default
        self.calls: list[str] = []

    async def user_can_read(self, *, token: str, repo_full_name: str) -> bool:
        self.calls.append(repo_full_name)
        return self.verdicts.get(repo_full_name, self.default)


def checker(probe: Probe | None = None) -> RepoAccessChecker:
    """A per-viewer access checker over an in-memory probe."""
    return RepoAccessChecker(probe=probe or Probe(), tokens=lambda _user: "gho_test")


def registry(clients: Mapping[int, FakeGitHubClient]) -> FakeInstallationClients[Any]:
    """A per-installation client registry answering only the given installation ids."""
    return FakeInstallationClients(dict(clients))


class FakeClient(FakeGitHubClient):
    """The shared GitHub fake, recording reads and refusing named pull requests."""

    def __init__(self, *args: Any, installation_id: int = 555, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.installation_id = installation_id
        self.calls: list[str] = []
        #: Pull requests whose detail read GitHub refuses, keyed ``(repo, number)``.
        self.refused: set[tuple[str, int]] = set()

    async def list_open_pull_requests(self, full_name: str) -> list[Any]:
        self.calls.append(f"list:{full_name}")
        return await super().list_open_pull_requests(full_name)

    async def get_pull_request(self, full_name: str, number: int) -> Any:
        self.calls.append(f"detail:{full_name}#{number}")
        if (full_name, number) in self.refused:
            raise GitHubError(f"{full_name}#{number} is unavailable")
        return await super().get_pull_request(full_name, number)

    async def list_check_runs(self, full_name: str, ref: str) -> list[Any]:
        self.calls.append(f"checks:{full_name}@{ref}")
        return await super().list_check_runs(full_name, ref)

    async def compare_commits(self, full_name: str, base: str, head: str) -> int:
        self.calls.append(f"compare:{full_name}:{base}..{head}")
        return await super().compare_commits(full_name, base, head)


def pull(
    full_name: str = _REPO,
    number: int = 42,
    *,
    title: str | None = None,
    head_sha: str = _HEAD,
    draft: bool = False,
    author: str = "octocat",
    changed_files: int = 0,
    additions: int = 0,
    deletions: int = 0,
    updated_at: str = "2026-01-01T00:00:00Z",
    created_at: str = "2025-12-01T00:00:00Z",
) -> GitHubPullRequest:
    """Build a typed GitHub pull request for the fake client."""
    return GitHubPullRequest(
        repo_full_name=full_name,
        private=False,
        number=number,
        title=title or f"fix: change {number}",
        url=f"https://github.com/{full_name}/pull/{number}",
        head_branch="fix/branch",
        base_branch="main",
        head_sha=head_sha,
        default_branch="main",
        body="",
        author_login=author,
        draft=draft,
        changed_files=changed_files,
        additions=additions,
        deletions=deletions,
        updated_at=updated_at,
        created_at=created_at,
    )


def run(name: str, conclusion: str | None = None, *, status: str = "completed") -> CheckRun:
    """Build one check run for a head commit."""
    return CheckRun(name=name, status=status, conclusion=conclusion)


async def add_review(
    session_factory: Any,
    inbox: Inbox,
    *,
    number: int,
    status: str = "done",
    reviewed_sha: str | None = None,
    findings: int = 0,
    created_at: dt.datetime | None = None,
    finished_at: dt.datetime | None = None,
    run_finished_at: dt.datetime | None = None,
) -> uuid.UUID:
    """Add one session with one target for the inbox's repository.

    ``created_at`` orders the join (the most recent submission wins) and
    ``run_finished_at`` is what a finished review reports as when it finished.
    """
    async with session_factory() as session:
        workspace = await session.get(Workspace, inbox.workspace_id)
        user = await session.get(User, inbox.user_id)
        repository = await session.get(Repository, inbox.repository_id)
        assert workspace is not None and user is not None and repository is not None
        review = ReviewSession(
            workspace_id=workspace.id,
            title=f"fix: change {number}",
            name=f"{repository.full_name}#{number} - Jan 1",
            prompt="Focus on security",
            status=status,
            model="claude-sonnet-4",
            provider="Anthropic",
            triggered_by_user_id=user.id,
            finished_at=finished_at,
        )
        if created_at is not None:
            review.created_at = created_at
        session.add(review)
        await session.flush()
        target = SessionTarget(
            session_id=review.id,
            repository_id=repository.id,
            number=number,
            title=f"fix: change {number}",
            url=f"https://github.com/{repository.full_name}/pull/{number}",
            head_branch="fix/branch",
            status=status,
            reviewed_sha=reviewed_sha,
        )
        session.add(target)
        await session.flush()
        if run_finished_at is not None:
            session.add(
                SessionTargetRun(
                    target_id=target.id,
                    attempt=1,
                    status=status,
                    finished_at=run_finished_at,
                )
            )
        for index in range(findings):
            session.add(
                Finding(
                    target_id=target.id,
                    path=f"src/file{index}.py",
                    line=1,
                    severity="error",
                    category="security",
                    message="A finding",
                    confidence=0.9,
                )
            )
        await session.commit()
        return review.id


@pytest_asyncio.fixture
async def inbox(session_factory: Any) -> Inbox:
    """One workspace, member, and connected repository, with no sessions yet."""
    async with session_factory() as session:
        workspace, user, repository = await seed_workspace(session)
        return Inbox(
            workspace_id=workspace.id,
            user_id=user.id,
            repository_id=repository.id,
        )


def rows(body: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """The page's rows, typed loosely for assertions."""
    return list(body["items"])


def reviews(body: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """The page's review columns, typed loosely for assertions."""
    return [row["review"] for row in body["items"]]


def _without_generated_at(body: Mapping[str, Any]) -> dict[str, Any]:
    """A page with its generation timestamp removed, for comparing two reads."""
    return {key: value for key, value in body.items() if key != "generatedAt"}


# --- the review join (spec v3 §2) -------------------------------------------


async def test_a_pull_request_no_session_covered_is_never_reviewed(
    inbox: Inbox, build_harness: Any
) -> None:
    # Given a workspace with one open pull request and no session at all
    harness: ApiHarness = await build_harness(
        user_id=inbox.user_id,
        github_client=FakeClient({_REPO: [pull()]}),
        repo_access=checker(),
    )

    # When the inbox is read
    response = await harness.client.get("/api/pull-requests")

    # Then the row reports nothing known about a review
    assert response.status_code == 200
    assert reviews(response.json())[0] == {
        "state": "never",
        "sessionId": None,
        "reviewedSha": None,
        "commitsSinceReview": 0,
        "findingsCount": 0,
        "progress": None,
        "step": None,
        "reviewedAt": None,
    }


async def test_a_running_review_reports_progress_and_step(
    inbox: Inbox, session_factory: Any, build_harness: Any
) -> None:
    # Given a session whose only target is running
    review_id = await add_review(session_factory, inbox, number=42, status="running")
    harness: ApiHarness = await build_harness(
        user_id=inbox.user_id,
        github_client=FakeClient({_REPO: [pull()]}),
        repo_access=checker(),
    )

    # When the inbox is read
    response = await harness.client.get("/api/pull-requests")

    # Then the row is running, linked to the session, with live progress
    review = reviews(response.json())[0]
    assert review["state"] == "running"
    assert review["sessionId"] == str(review_id)
    assert 0 <= review["progress"] <= 100
    assert review["step"]
    assert review["reviewedAt"] is None


async def test_a_finished_review_reports_its_findings_and_when_it_finished(
    inbox: Inbox, session_factory: Any, build_harness: Any
) -> None:
    # Given a completed review of the current head, with findings and a run
    finished = dt.datetime(2026, 2, 3, 4, 5, tzinfo=dt.UTC)
    await add_review(
        session_factory,
        inbox,
        number=42,
        status="done",
        reviewed_sha=_HEAD,
        findings=2,
        run_finished_at=finished,
    )
    harness: ApiHarness = await build_harness(
        user_id=inbox.user_id,
        github_client=FakeClient({_REPO: [pull()]}),
        repo_access=checker(),
    )

    # When the inbox is read
    response = await harness.client.get("/api/pull-requests")

    # Then the review is current: the head SHA, no distance, the findings, and
    # the attempt's finish time
    review = reviews(response.json())[0]
    assert review["state"] == "reviewed"
    assert review["reviewedSha"] == _HEAD
    assert review["commitsSinceReview"] == 0
    assert review["findingsCount"] == 2
    assert review["progress"] is None
    assert review["reviewedAt"] is not None


@pytest.mark.parametrize("status", ["failed", "cancelled", "skipped"])
async def test_an_attempt_that_produced_nothing_reads_as_failed(
    inbox: Inbox, session_factory: Any, build_harness: Any, status: str
) -> None:
    # Given a session target that failed, was cancelled, or was skipped
    review_id = await add_review(session_factory, inbox, number=42, status=status)
    harness: ApiHarness = await build_harness(
        user_id=inbox.user_id,
        github_client=FakeClient({_REPO: [pull()]}),
        repo_access=checker(),
    )

    # When the inbox is read
    response = await harness.client.get("/api/pull-requests")

    # Then the row offers the session to retry from rather than a review
    review = reviews(response.json())[0]
    assert review["state"] == "failed"
    assert review["sessionId"] == str(review_id)
    assert review["progress"] is None


async def test_an_in_flight_review_outranks_an_older_completed_one(
    inbox: Inbox, session_factory: Any, build_harness: Any
) -> None:
    # Given a reviewed pull request that a newer session is now reviewing again
    await add_review(
        session_factory,
        inbox,
        number=42,
        status="done",
        reviewed_sha=_HEAD,
        created_at=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
    )
    running_id = await add_review(
        session_factory,
        inbox,
        number=42,
        status="running",
        created_at=dt.datetime(2026, 2, 1, tzinfo=dt.UTC),
    )
    harness: ApiHarness = await build_harness(
        user_id=inbox.user_id,
        github_client=FakeClient({_REPO: [pull()]}),
        repo_access=checker(),
    )

    # When the inbox is read
    response = await harness.client.get("/api/pull-requests")

    # Then the row shows the review that is still running
    review = reviews(response.json())[0]
    assert review["state"] == "running"
    assert review["sessionId"] == str(running_id)


# --- staleness (spec v3 §2) -------------------------------------------------


async def test_a_review_behind_the_head_counts_the_commits_since_it(
    inbox: Inbox, session_factory: Any, build_harness: Any
) -> None:
    # Given a review of an earlier commit and a head that moved two commits on
    await add_review(
        session_factory, inbox, number=42, status="done", reviewed_sha=_REVIEWED
    )
    client = FakeClient(
        {_REPO: [pull(head_sha=_HEAD)]},
        compares={(_REPO, _REVIEWED, _HEAD): 2},
    )
    harness: ApiHarness = await build_harness(
        user_id=inbox.user_id, github_client=client, repo_access=checker()
    )

    # When the inbox is read
    response = await harness.client.get("/api/pull-requests")

    # Then the row reports the reviewed SHA and how far behind it is
    review = reviews(response.json())[0]
    assert review["state"] == "reviewed"
    assert review["reviewedSha"] == _REVIEWED
    assert review["commitsSinceReview"] == 2
    assert f"compare:{_REPO}:{_REVIEWED}..{_HEAD}" in client.calls


async def test_a_review_github_will_not_compare_reports_an_unknown_distance(
    inbox: Inbox, session_factory: Any, build_harness: Any
) -> None:
    # Given a review whose SHA GitHub can no longer compare with the head (the
    # fake holds no comparison for the pair, as a force-push leaves it)
    await add_review(
        session_factory, inbox, number=42, status="done", reviewed_sha=_REVIEWED
    )
    harness: ApiHarness = await build_harness(
        user_id=inbox.user_id,
        github_client=FakeClient({_REPO: [pull(head_sha=_HEAD)]}),
        repo_access=checker(),
    )

    # When the inbox is read
    response = await harness.client.get("/api/pull-requests")

    # Then the row is still served, with an unknown distance rather than a count
    assert response.status_code == 200
    review = reviews(response.json())[0]
    assert review["state"] == "reviewed"
    assert review["commitsSinceReview"] is None
    assert response.json()["summary"]["stale"] == 1


async def test_a_review_with_no_recorded_sha_makes_no_freshness_claim(
    inbox: Inbox, session_factory: Any, build_harness: Any
) -> None:
    # Given a review recorded before the reviewed SHA column existed
    await add_review(session_factory, inbox, number=42, status="done", reviewed_sha=None)
    client = FakeClient({_REPO: [pull(head_sha=_HEAD)]})
    harness: ApiHarness = await build_harness(
        user_id=inbox.user_id, github_client=client, repo_access=checker()
    )

    # When the inbox is read
    response = await harness.client.get("/api/pull-requests")

    # Then it reads as reviewed, with no SHA and no distance claimed...
    review = reviews(response.json())[0]
    assert review["state"] == "reviewed"
    assert review["reviewedSha"] is None
    assert review["commitsSinceReview"] is None
    # ...nothing was compared, because there is nothing to compare...
    assert not [call for call in client.calls if call.startswith("compare:")]
    # ...and because the app cannot tell whether it is behind, it counts as work
    # worth redoing
    assert response.json()["summary"] == {
        "total": 1,
        "needsReview": 1,
        "stale": 1,
        "running": 0,
    }

    # When the inbox is filtered to stale reviews
    stale = await harness.client.get("/api/pull-requests", params={"review": "stale"})

    # Then that review is selected, and the page carries its options and totals
    assert [row["number"] for row in stale.json()["items"]] == [42]
    assert stale.json()["total"] == 1
    assert stale.json()["summary"]["stale"] == 1
    hints = {
        option["value"]: option["hint"]
        for option in stale.json()["filterOptions"]["reviews"]
    }
    assert hints["stale"] == "1"
    assert hints["reviewed"] == "1"


async def test_the_stale_filter_selects_exactly_the_reviews_behind_the_head(
    inbox: Inbox, session_factory: Any, build_harness: Any
) -> None:
    # Given one current review, one behind it, and one never reviewed
    await add_review(session_factory, inbox, number=1, status="done", reviewed_sha=_HEAD)
    await add_review(
        session_factory, inbox, number=2, status="done", reviewed_sha=_REVIEWED
    )
    client = FakeClient(
        {
            _REPO: [
                pull(number=1, head_sha=_HEAD),
                pull(number=2, head_sha=_HEAD),
                pull(number=3, head_sha=_HEAD),
            ]
        },
        compares={(_REPO, _REVIEWED, _HEAD): 3},
    )
    harness: ApiHarness = await build_harness(
        user_id=inbox.user_id, github_client=client, repo_access=checker()
    )

    # When the inbox is filtered to stale reviews, and to reviewed ones
    stale = await harness.client.get("/api/pull-requests", params={"review": "stale"})
    reviewed = await harness.client.get("/api/pull-requests", params={"review": "reviewed"})

    # Then stale selects the review behind the head only, while reviewed selects
    # the state (so it includes the stale row)
    assert [row["number"] for row in stale.json()["items"]] == [2]
    assert stale.json()["total"] == 1
    assert sorted(row["number"] for row in reviewed.json()["items"]) == [1, 2]
    assert stale.json()["summary"] == {
        "total": 1,
        "needsReview": 1,
        "stale": 1,
        "running": 0,
    }


# --- filters, paging, sorting (spec v3 §3) ----------------------------------


async def test_drafts_are_hidden_unless_they_are_asked_for(
    inbox: Inbox, build_harness: Any
) -> None:
    # Given one draft and one ready pull request
    harness: ApiHarness = await build_harness(
        user_id=inbox.user_id,
        github_client=FakeClient(
            {_REPO: [pull(number=1, draft=True), pull(number=2, draft=False)]}
        ),
        repo_access=checker(),
    )

    # When the inbox is read by default and then with drafts included
    default = await harness.client.get("/api/pull-requests")
    with_drafts = await harness.client.get("/api/pull-requests", params={"drafts": "1"})

    # Then the draft is only there when it was asked for
    assert [row["number"] for row in default.json()["items"]] == [2]
    assert default.json()["total"] == 1
    assert sorted(row["number"] for row in with_drafts.json()["items"]) == [1, 2]
    assert with_drafts.json()["total"] == 2


async def test_search_matches_title_repository_number_and_author(
    inbox: Inbox, build_harness: Any
) -> None:
    # Given three pull requests with nothing in common but their own fields
    harness: ApiHarness = await build_harness(
        user_id=inbox.user_id,
        github_client=FakeClient(
            {
                _REPO: [
                    pull(number=11, title="Harden token refresh"),
                    pull(number=22, title="Add widget", author="alice"),
                    pull(number=33, title="Drop dead code", author="bob"),
                ]
            }
        ),
        repo_access=checker(),
    )

    async def numbers(params: Mapping[str, str]) -> list[int]:
        response = await harness.client.get("/api/pull-requests", params=params)
        assert response.status_code == 200
        return sorted(row["number"] for row in response.json()["items"])

    # When the inbox is searched by title, by number, by reference, and by author
    assert await numbers({"q": "harden"}) == [11]
    assert await numbers({"q": "#22"}) == [22]
    assert await numbers({"q": f"{_REPO}#33"}) == [33]
    assert await numbers({"q": "alice"}) == [22]
    assert await numbers({"q": _REPO}) == [11, 22, 33]


async def test_the_checks_filter_selects_by_ci_state(
    inbox: Inbox, build_harness: Any
) -> None:
    # Given one green and one red pull request
    harness: ApiHarness = await build_harness(
        user_id=inbox.user_id,
        github_client=FakeClient(
            {_REPO: [pull(number=1, head_sha="sha1"), pull(number=2, head_sha="sha2")]},
            checks={
                (_REPO, "sha1"): [run("ci", "success")],
                (_REPO, "sha2"): [run("ci", "failure")],
            },
        ),
        repo_access=checker(),
    )

    # When the inbox is filtered to failing checks
    response = await harness.client.get("/api/pull-requests", params={"checks": "failing"})

    # Then only the red pull request is returned, and it carries its rollup
    assert response.status_code == 200
    assert [row["number"] for row in response.json()["items"]] == [2]
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["checks"] == {
        "state": "failing",
        "total": 1,
        "passing": 0,
    }


async def test_totals_and_pages_describe_the_filtered_set(
    inbox: Inbox, build_harness: Any
) -> None:
    # Given five open pull requests, one of them a draft
    harness: ApiHarness = await build_harness(
        user_id=inbox.user_id,
        github_client=FakeClient(
            {_REPO: [pull(number=number, draft=number == 5) for number in range(1, 6)]}
        ),
        repo_access=checker(),
    )

    # When a two-row page of the non-draft set is requested
    response = await harness.client.get(
        "/api/pull-requests", params={"pageSize": 2, "page": 2}
    )

    # Then the totals count the filtered set, not the page or the workspace
    body = response.json()
    assert (body["page"], body["pageSize"]) == (2, 2)
    assert body["total"] == 4
    assert body["totalPages"] == 2
    assert len(body["items"]) == 2
    assert body["summary"]["total"] == 4


async def test_a_page_past_the_end_and_a_huge_page_size_are_clamped(
    inbox: Inbox, build_harness: Any
) -> None:
    # Given two open pull requests
    harness: ApiHarness = await build_harness(
        user_id=inbox.user_id,
        github_client=FakeClient({_REPO: [pull(number=1), pull(number=2)]}),
        repo_access=checker(),
    )

    # When a page nobody asked for is requested
    response = await harness.client.get(
        "/api/pull-requests", params={"page": 99, "pageSize": 5000}
    )

    # Then the answer is a valid empty page rather than an error
    assert response.status_code == 200
    body = response.json()
    assert (body["page"], body["pageSize"]) == (99, 100)
    assert body["items"] == []
    assert body["total"] == 2


async def test_size_sort_orders_by_diff_size_and_creation_sort_by_age(
    inbox: Inbox, build_harness: Any
) -> None:
    # Given three pull requests with different sizes and creation dates
    harness: ApiHarness = await build_harness(
        user_id=inbox.user_id,
        github_client=FakeClient(
            {
                _REPO: [
                    pull(
                        number=1,
                        additions=5,
                        deletions=1,
                        created_at="2026-01-01T00:00:00Z",
                    ),
                    pull(
                        number=2,
                        additions=400,
                        deletions=20,
                        created_at="2025-06-01T00:00:00Z",
                    ),
                    pull(
                        number=3,
                        additions=40,
                        deletions=2,
                        created_at="2026-03-01T00:00:00Z",
                    ),
                ]
            }
        ),
        repo_access=checker(),
    )

    async def numbers(sort: str) -> list[int]:
        response = await harness.client.get("/api/pull-requests", params={"sort": sort})
        return [row["number"] for row in response.json()["items"]]

    # When the inbox is sorted by size and by creation
    by_size = await numbers("size_desc")
    by_creation = await numbers("created_desc")

    # Then the biggest diff leads, and the newest pull request leads
    assert by_size == [2, 3, 1]
    assert by_creation == [3, 1, 2]


async def test_staleness_sort_leads_with_the_reviews_behind_the_head(
    inbox: Inbox, session_factory: Any, build_harness: Any
) -> None:
    # Given a current review, a stale one, a failed attempt, and a pull request
    # nobody has looked at
    await add_review(session_factory, inbox, number=1, status="done", reviewed_sha=_HEAD)
    await add_review(
        session_factory, inbox, number=2, status="done", reviewed_sha=_REVIEWED
    )
    await add_review(session_factory, inbox, number=3, status="failed")
    harness: ApiHarness = await build_harness(
        user_id=inbox.user_id,
        github_client=FakeClient(
            {_REPO: [pull(number=number) for number in (1, 2, 3, 4)]},
            compares={(_REPO, _REVIEWED, _HEAD): 3},
        ),
        repo_access=checker(),
    )

    # When the inbox is sorted by staleness, and by recency
    stale_first = await harness.client.get(
        "/api/pull-requests", params={"sort": "staleness_desc"}
    )
    recent_first = await harness.client.get(
        "/api/pull-requests", params={"sort": "updated_desc"}
    )

    # Then reviewed work leads (the one behind the head first), then the attempt
    # that needs a retry, then work nobody has looked at
    assert [row["number"] for row in stale_first.json()["items"]] == [2, 1, 3, 4]
    # ...while the default order is newest activity first, which every row here
    # shares
    assert sorted(row["number"] for row in recent_first.json()["items"]) == [1, 2, 3, 4]


async def test_filter_options_count_the_whole_set_not_the_page(
    inbox: Inbox, session_factory: Any, build_harness: Any
) -> None:
    # Given one stale review and one never-reviewed pull request, plus a second
    # connected repository with nothing open
    await add_review(
        session_factory, inbox, number=1, status="done", reviewed_sha=_REVIEWED
    )
    async with session_factory() as session:
        await add_installation(
            session,
            inbox.workspace_id,
            installation_id=777,
            account_login="widgets",
            repositories=["widgets/app"],
        )
    harness: ApiHarness = await build_harness(
        user_id=inbox.user_id,
        github_client=FakeClient({_REPO: [pull(number=1), pull(number=2)]}),
        repo_access=checker(),
    )

    # When a single row is asked for
    response = await harness.client.get("/api/pull-requests", params={"pageSize": 1})

    # Then the filter options describe the whole set, not that page
    body = response.json()
    assert body["total"] == 2
    assert len(body["items"]) == 1
    options = body["filterOptions"]
    assert [(option["value"], option["hint"]) for option in options["repositories"]] == [
        (_REPO, "2"),
        ("widgets/app", "0"),
    ]
    assert [option["value"] for option in options["reviews"]] == [
        "never",
        "queued",
        "running",
        "reviewed",
        "stale",
        "failed",
    ]
    hints = {option["value"]: option["hint"] for option in options["reviews"]}
    assert hints["never"] == "1"
    assert hints["reviewed"] == "1"
    assert hints["stale"] == "1"
    assert [option["value"] for option in options["checks"]] == [
        "passing",
        "failing",
        "pending",
        "none",
    ]


async def test_the_summary_counts_the_backlog_the_header_states(
    inbox: Inbox, session_factory: Any, build_harness: Any
) -> None:
    # Given a stale review, a running one, and a pull request nobody looked at
    await add_review(
        session_factory, inbox, number=1, status="done", reviewed_sha=_REVIEWED
    )
    await add_review(session_factory, inbox, number=2, status="queued")
    harness: ApiHarness = await build_harness(
        user_id=inbox.user_id,
        github_client=FakeClient(
            {
                _REPO: [
                    pull(number=1),
                    pull(number=2),
                    pull(number=3),
                ]
            }
        ),
        repo_access=checker(),
    )

    # When the inbox is read
    response = await harness.client.get("/api/pull-requests")

    # Then the header counts the backlog: needs review is never plus stale
    assert response.json()["summary"] == {
        "total": 3,
        "needsReview": 2,
        "stale": 1,
        "running": 1,
    }


# --- degradation and access -------------------------------------------------


async def test_a_parked_repository_disappears_from_the_inbox(
    inbox: Inbox, session_factory: Any, build_harness: Any
) -> None:
    # Given two connected repositories, one of them parked
    async with session_factory() as session:
        installation = await add_installation(
            session,
            inbox.workspace_id,
            installation_id=777,
            account_login="widgets",
            repositories=["widgets/app"],
        )
        parked = await session.get(Repository, inbox.repository_id)
        assert parked is not None
        parked.enabled = False
        await session.commit()
    assert installation is not None
    client = FakeClient(
        {_REPO: [pull()], "widgets/app": [pull(full_name="widgets/app", number=9)]}
    )
    harness: ApiHarness = await build_harness(
        user_id=inbox.user_id, github_client=client, repo_access=checker()
    )

    # When the inbox is read
    response = await harness.client.get("/api/pull-requests")

    # Then the parked repository is neither listed nor read
    assert response.status_code == 200
    assert [row["repository"]["fullName"] for row in response.json()["items"]] == [
        "widgets/app"
    ]
    assert [option["value"] for option in response.json()["filterOptions"]["repositories"]] == [
        "widgets/app"
    ]
    assert f"list:{_REPO}" not in client.calls


async def test_a_repository_github_refuses_does_not_fail_the_page(
    inbox: Inbox, session_factory: Any, build_harness: Any
) -> None:
    # Given two connected repositories, one of which GitHub refuses
    async with session_factory() as session:
        await add_installation(
            session,
            inbox.workspace_id,
            installation_id=777,
            account_login="widgets",
            repositories=["widgets/app"],
        )
    harness: ApiHarness = await build_harness(
        user_id=inbox.user_id,
        github_client=FakeClient(
            {_REPO: [pull()], "widgets/app": []}, failing={_REPO}
        ),
        repo_access=checker(),
    )

    # When the inbox is read
    response = await harness.client.get("/api/pull-requests")

    # Then the repository that answered is served and the refused one is skipped
    assert response.status_code == 200
    body = response.json()
    assert body["items"] == []
    assert body["total"] == 0
    assert [option["value"] for option in body["filterOptions"]["repositories"]] == [
        "widgets/app"
    ]


async def test_a_workspace_with_nothing_connected_answers_an_empty_inbox(
    session_factory: Any, build_harness: Any
) -> None:
    # Given a workspace with no connected repository at all
    async with session_factory() as session:
        _workspace, user = await seed_empty_workspace(session)
    harness: ApiHarness = await build_harness(
        user_id=user.id, github_client=FakeClient(), repo_access=checker()
    )

    # When the inbox is read
    response = await harness.client.get("/api/pull-requests")

    # Then it is an empty page — the app's install-the-App state, not an error
    assert response.status_code == 200
    body = response.json()
    assert body["items"] == []
    assert body["total"] == 0
    assert body["totalPages"] == 0
    assert body["filterOptions"]["repositories"] == []


async def test_an_inbox_whose_every_repository_fails_is_a_502(
    inbox: Inbox, build_harness: Any
) -> None:
    # Given a workspace whose only repository GitHub refuses
    harness: ApiHarness = await build_harness(
        user_id=inbox.user_id,
        github_client=FakeClient({_REPO: [pull()]}, failing={_REPO}),
        repo_access=checker(),
    )

    # When the inbox is read
    response = await harness.client.get("/api/pull-requests")

    # Then the empty inbox is reported as a failure rather than as "nothing open"
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "pull_requests_unavailable"


async def test_a_pull_request_github_refuses_degrades_to_an_empty_row(
    inbox: Inbox, build_harness: Any
) -> None:
    # Given two open pull requests, one of which GitHub refuses to detail
    client = FakeClient({_REPO: [pull(number=1), pull(number=2)]})
    client.refused.add((_REPO, 1))
    harness: ApiHarness = await build_harness(
        user_id=inbox.user_id, github_client=client, repo_access=checker()
    )

    # When the inbox is read
    response = await harness.client.get("/api/pull-requests")

    # Then the page still answers, with zeros and no CI for that row only
    assert response.status_code == 200
    body = response.json()
    degraded = next(row for row in body["items"] if row["number"] == 1)
    assert (degraded["changedFiles"], degraded["additions"], degraded["deletions"]) == (0, 0, 0)
    assert degraded["checks"] == {"state": "none", "total": 0, "passing": 0}


async def test_the_inbox_hides_a_repository_the_viewer_cannot_read(
    inbox: Inbox, session_factory: Any, build_harness: Any
) -> None:
    # Given a repository the viewer may not read, and one they may
    async with session_factory() as session:
        await add_installation(
            session,
            inbox.workspace_id,
            installation_id=777,
            account_login="widgets",
            repositories=["widgets/app"],
        )
    harness: ApiHarness = await build_harness(
        user_id=inbox.user_id,
        github_client=FakeClient(
            {
                _REPO: [pull()],
                "widgets/app": [pull(full_name="widgets/app", number=9)],
            }
        ),
        repo_access=checker(Probe({_REPO: False, "widgets/app": True})),
    )

    # When the inbox is read
    response = await harness.client.get("/api/pull-requests")

    # Then only the readable repository's rows and options come back
    body = response.json()
    assert [row["repository"]["fullName"] for row in body["items"]] == ["widgets/app"]
    assert [option["value"] for option in body["filterOptions"]["repositories"]] == [
        "widgets/app"
    ]


async def test_the_viewers_own_review_stays_visible_without_a_read_check(
    inbox: Inbox, session_factory: Any, build_harness: Any
) -> None:
    # Given a viewer whose access cannot be verified, on a pull request they
    # themselves put a review on
    await add_review(session_factory, inbox, number=42, status="done", reviewed_sha=_HEAD)
    harness: ApiHarness = await build_harness(
        user_id=inbox.user_id,
        github_client=FakeClient({_REPO: [pull()]}),
        repo_access=checker(Probe(default=False)),
    )

    # When the inbox is read
    response = await harness.client.get("/api/pull-requests")

    # Then their own row is there — pre-flight already verified them at submit
    # time — and the row they never reviewed is not
    assert response.status_code == 200
    assert [row["number"] for row in response.json()["items"]] == [42]


# --- the expensive tier -----------------------------------------------------


async def test_the_expensive_tier_is_read_once_per_pull_request(
    inbox: Inbox, build_harness: Any
) -> None:
    # Given two open pull requests with their diff size and CI available
    client = FakeClient(
        {
            _REPO: [
                pull(number=1, head_sha="sha1", changed_files=3, additions=40, deletions=7),
                pull(number=2, head_sha="sha2", changed_files=1, additions=4, deletions=0),
            ]
        },
        checks={
            (_REPO, "sha1"): [run("ci", "success")],
            (_REPO, "sha2"): [run("ci", "failure")],
        },
    )
    harness: ApiHarness = await build_harness(
        user_id=inbox.user_id, github_client=client, repo_access=checker()
    )

    # When the inbox is read twice inside the cache's lifetime
    first = await harness.client.get("/api/pull-requests")
    reads = list(client.calls)
    second = await harness.client.get("/api/pull-requests")

    # Then one listing, one detail, and one check-run read per pull request...
    assert sorted(reads) == sorted(
        [
            f"list:{_REPO}",
            "detail:acme/api#1",
            "checks:acme/api@sha1",
            "detail:acme/api#2",
            "checks:acme/api@sha2",
        ]
    )
    # ...the second read asked GitHub nothing...
    assert client.calls == reads
    # ...and both answered with the hydrated numbers
    assert _without_generated_at(second.json()) == _without_generated_at(first.json())
    assert first.json()["items"][0]["checks"] == {
        "state": "passing",
        "total": 1,
        "passing": 1,
    }
    assert first.json()["items"][1]["changedFiles"] == 1


async def test_a_page_does_not_hydrate_rows_it_does_not_return(
    inbox: Inbox, build_harness: Any
) -> None:
    # Given four open pull requests
    client = FakeClient({_REPO: [pull(number=number) for number in range(1, 5)]})
    harness: ApiHarness = await build_harness(
        user_id=inbox.user_id, github_client=client, repo_access=checker()
    )

    # When a single row is asked for
    response = await harness.client.get("/api/pull-requests", params={"pageSize": 1})

    # Then only that row cost a per-pull-request read
    assert response.status_code == 200
    assert response.json()["total"] == 4
    assert len(response.json()["items"]) == 1
    assert len([call for call in client.calls if call.startswith("detail:")]) == 1


async def test_an_unreadable_installation_does_not_fail_the_page(
    inbox: Inbox, session_factory: Any, build_harness: Any
) -> None:
    # Given a workspace holding two installations, one of which cannot be read
    async with session_factory() as session:
        await add_installation(
            session,
            inbox.workspace_id,
            installation_id=777,
            account_login="widgets",
            repositories=["widgets/app"],
        )
    harness: ApiHarness = await build_harness(
        user_id=inbox.user_id,
        github_clients=registry({555: FakeClient({_REPO: [pull()]})}),
        repo_access=checker(),
    )

    # When the inbox is read
    response = await harness.client.get("/api/pull-requests")

    # Then the installation that could be read is served
    assert response.status_code == 200
    assert [row["repository"]["fullName"] for row in response.json()["items"]] == [_REPO]


async def test_a_second_installations_rows_are_read_through_its_own_client(
    inbox: Inbox, session_factory: Any, build_harness: Any
) -> None:
    # Given a workspace holding two installations, each with a repository
    async with session_factory() as session:
        await add_installation(
            session,
            inbox.workspace_id,
            installation_id=777,
            account_login="widgets",
            repositories=["widgets/app"],
        )
    acme = FakeClient({_REPO: [pull()]}, installation_id=555)
    widgets = FakeClient(
        {"widgets/app": [pull(full_name="widgets/app", number=9)]}, installation_id=777
    )
    harness: ApiHarness = await build_harness(
        user_id=inbox.user_id,
        github_clients=registry({555: acme, 777: widgets}),
        repo_access=checker(),
    )

    # When the inbox is read
    response = await harness.client.get("/api/pull-requests")

    # Then both installations contributed their own rows
    assert sorted(row["repository"]["fullName"] for row in response.json()["items"]) == [
        "acme/api",
        "widgets/app",
    ]
