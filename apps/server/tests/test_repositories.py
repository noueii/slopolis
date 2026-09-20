"""Connected repositories and open pull requests."""

from __future__ import annotations

from typing import Any

from slopolis_core.github.models import CheckRun, GitHubPullRequest

from .conftest import ApiHarness, FakeGitHubClient


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


async def test_list_repository_pulls_returns_open_prs(
    seeded: Any, build_harness: Any
) -> None:
    # Given a repository whose listing omits the numbers, as GitHub's does
    listed = _pull("acme/api", 42, "Add guard")
    detail = listed.model_copy(
        update={"changed_files": 5, "additions": 76, "deletions": 0}
    )
    client = FakeGitHubClient(
        {"acme/api": [listed]},
        details={("acme/api", 42): detail},
        checks={
            ("acme/api", "abc123"): [
                CheckRun(name="ci", status="completed", conclusion="failure"),
                CheckRun(name="lint", status="completed", conclusion="success"),
            ]
        },
    )
    harness: ApiHarness = await build_harness(
        user_id=seeded.user_id, github_client=client
    )

    # When the repo's pull requests are listed
    response = await harness.client.get("/api/repositories/acme/api/pulls")

    # Then the PR carries the single-PR read's numbers and its CI rollup
    assert response.status_code == 200
    body = response.json()
    assert body["repository"]["fullName"] == "acme/api"
    assert body["repository"]["openPrCount"] == 1
    pull = body["pullRequests"][0]
    assert pull["number"] == 42
    assert (pull["changedFiles"], pull["additions"], pull["deletions"]) == (5, 76, 0)
    assert pull["checks"] == {"state": "failing", "total": 2, "passing": 1}


async def test_list_repository_pulls_unknown_repo_is_404(
    seeded: Any, build_harness: Any
) -> None:
    # Given a workspace that does not own the requested repo
    harness: ApiHarness = await build_harness(user_id=seeded.user_id)

    # When an unknown repo's pulls are requested
    response = await harness.client.get("/api/repositories/acme/unknown/pulls")

    # Then a 404 with the standard error envelope is returned
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "repository_not_found"
