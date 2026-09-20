"""Behavior tests for the bounded GitHub REST client (spec 10.6).

All GitHub traffic is faked with ``respx`` at ``https://api.github.com``; response
bodies come from :func:`test_github_helpers.fixture`, which derives a
schema-complete payload from the githubkit model so nothing is hand-maintained.
"""

from __future__ import annotations

import base64
from typing import Any

import httpx
import pytest
import respx
from githubkit import GitHub, TokenAuthStrategy
from githubkit_schemas.latest.models import (  # pyright: ignore[reportMissingTypeStubs]
    CheckRun,
    ContentFile,
    DiffEntry,
    FullRepository,
    PullRequest,
    RepositoryCollaboratorPermission,
    ReposOwnerRepoCommitsRefCheckRunsGetResponse200,
)
from test_github_helpers import fixture

from slopolis_core.github.auth import InstallationAuth
from slopolis_core.github.client import GitHubClient
from slopolis_core.github.errors import GitHubNotFoundError
from slopolis_core.github.limits import MAX_FILE_BYTES

_BASE = "https://api.github.com"
_REPO = "acme/widget"
_OWNER, _NAME = _REPO.split("/")
_TOKEN = "ghs_test"
_PULL_URL = "https://github.com/acme/widget/pull/7"


def _client() -> GitHubClient:
    return GitHubClient(
        GitHub(TokenAuthStrategy(_TOKEN)),
        auth=InstallationAuth.from_installation_token(_TOKEN),
    )


def _pull_body(**overrides: Any) -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "number": 7,
        "title": "Add widget",
        "html_url": _PULL_URL,
        "body": "please review",
        "head": {"ref": "feature/widget", "sha": "headsha"},
        "base": {"ref": "main", "sha": "basesha"},
        "user": {"login": "alice"},
        "changed_files": 5,
        "additions": 10,
        "deletions": 2,
    }
    defaults.update(overrides)
    return fixture(PullRequest, **defaults)


def _mock_repo(mock: respx.Router, *, private: bool = False, default_branch: str = "main") -> None:
    body = fixture(FullRepository, private=private, default_branch=default_branch)
    mock.get(f"/repos/{_OWNER}/{_NAME}").mock(return_value=httpx.Response(200, json=body))


def _mock_pull(mock: respx.Router, body: dict[str, Any]) -> None:
    def route(request: httpx.Request) -> httpx.Response:
        if "diff" in request.headers.get("accept", ""):
            return httpx.Response(200, text="")
        return httpx.Response(200, json=body)

    mock.get(f"/repos/{_OWNER}/{_NAME}/pulls/7").mock(side_effect=route)


def _mock_permission(mock: respx.Router, permission: str | None) -> None:
    url = f"/repos/{_OWNER}/{_NAME}/collaborators/alice/permission"
    if permission is None:
        mock.get(url).mock(return_value=httpx.Response(403, json={"message": "Forbidden"}))
    else:
        body = fixture(RepositoryCollaboratorPermission, permission=permission)
        mock.get(url).mock(return_value=httpx.Response(200, json=body))


@respx.mock(base_url=_BASE)
async def test_resolve_pr_returns_typed_pull(respx_mock: respx.Router) -> None:
    """Given a valid PR URL, resolve_pr returns the repo/number/title/privacy."""
    _mock_repo(respx_mock, private=True, default_branch="trunk")
    _mock_pull(respx_mock, _pull_body())

    pull = await _client().resolve_pr(_PULL_URL)

    assert pull.repo_full_name == _REPO
    assert pull.number == 7
    assert pull.title == "Add widget"
    assert pull.private is True
    assert pull.default_branch == "trunk"


@respx.mock(base_url=_BASE)
async def test_resolve_pr_rejects_malformed_url_without_request(
    respx_mock: respx.Router,
) -> None:
    """Given a malformed URL, resolve_pr raises not-found and sends no request."""
    with pytest.raises(GitHubNotFoundError):
        await _client().resolve_pr("https://example.com/not/a/pr")

    assert len(respx_mock.calls) == 0


@respx.mock(base_url=_BASE)
async def test_get_pull_request_404_raises_not_found(respx_mock: respx.Router) -> None:
    """Given a 404 from GitHub, get_pull_request raises GitHubNotFoundError."""
    respx_mock.get(f"/repos/{_OWNER}/{_NAME}/pulls/7").mock(
        return_value=httpx.Response(404, json={"message": "Not Found"})
    )

    with pytest.raises(GitHubNotFoundError):
        await _client().get_pull_request(_REPO, 7)


@respx.mock(base_url=_BASE)
async def test_get_pr_context_caps_files_and_truncates_diff(respx_mock: respx.Router) -> None:
    """Given 5 changed files and a 10-line diff, context honors both caps."""
    _mock_repo(respx_mock)
    _mock_pull(respx_mock, _pull_body())
    files = [fixture(DiffEntry, filename=f"file{i}.py") for i in range(5)]
    respx_mock.get(f"/repos/{_OWNER}/{_NAME}/pulls/7/files").mock(
        return_value=httpx.Response(200, json=files)
    )
    diff_text = "\n".join(f"line{i}" for i in range(10))

    def route(request: httpx.Request) -> httpx.Response:
        if "diff" in request.headers.get("accept", ""):
            return httpx.Response(200, text=diff_text)
        return httpx.Response(200, json=_pull_body())

    respx_mock.get(f"/repos/{_OWNER}/{_NAME}/pulls/7").mock(side_effect=route)

    ctx = await _client().get_pr_context(_REPO, 7, max_files=2, max_diff_lines=3)

    assert ctx.changed_files == ["file0.py", "file1.py"]
    assert ctx.diff.startswith("line0\nline1\nline2")
    assert "truncated at 3 lines" in ctx.diff


@respx.mock(base_url=_BASE)
async def test_read_file_truncates_over_byte_cap(respx_mock: respx.Router) -> None:
    """Given a file past MAX_FILE_BYTES, read_file clips it with a marker."""
    oversized = b"a" * (MAX_FILE_BYTES + 5000)
    body = fixture(
        ContentFile,
        type="file",
        encoding="base64",
        path="big.py",
        content=base64.b64encode(oversized).decode(),
    )
    respx_mock.get(f"/repos/{_OWNER}/{_NAME}/contents/big.py").mock(
        return_value=httpx.Response(200, json=body)
    )

    text = await _client().read_file(_REPO, "big.py", "abc")

    assert text.endswith(f"... [truncated at {MAX_FILE_BYTES} bytes]")
    assert len(text) < len(oversized)


@respx.mock(base_url=_BASE)
async def test_list_changed_paths_returns_ordered_paths(respx_mock: respx.Router) -> None:
    """Given a changed-file list, list_changed_paths preserves API order."""
    files = [fixture(DiffEntry, filename=name) for name in ("a.py", "b.py", "c.py")]
    respx_mock.get(f"/repos/{_OWNER}/{_NAME}/pulls/7/files").mock(
        return_value=httpx.Response(200, json=files)
    )

    paths = await _client().list_changed_paths(_REPO, 7)

    assert paths == ["a.py", "b.py", "c.py"]


@respx.mock(base_url=_BASE)
async def test_list_check_runs_returns_the_head_commits_runs(
    respx_mock: respx.Router,
) -> None:
    """Given a commit with two check runs, both are returned with their conclusion."""
    respx_mock.get(f"/repos/{_OWNER}/{_NAME}/commits/headsha/check-runs").mock(
        return_value=httpx.Response(
            200,
            json=fixture(
                ReposOwnerRepoCommitsRefCheckRunsGetResponse200,
                total_count=2,
                check_runs=[
                    fixture(
                        CheckRun,
                        name="ci",
                        status="completed",
                        conclusion="failure",
                    ),
                    fixture(
                        CheckRun,
                        name="lint",
                        status="completed",
                        conclusion="success",
                    ),
                ],
            ),
        )
    )

    runs = await _client().list_check_runs(_REPO, "headsha")

    assert [(run.name, run.status, run.conclusion) for run in runs] == [
        ("ci", "completed", "failure"),
        ("lint", "completed", "success"),
    ]


@pytest.mark.parametrize(
    ("private", "permission", "required", "expected"),
    [
        (False, "read", None, False),
        (False, "write", None, True),
        (True, "read", None, True),
        (True, "none", None, False),
        (False, None, None, False),
        # An override loosens a public repo from write to read.
        (False, "read", "read", True),
        (False, "none", "read", False),
        # An override tightens a private repo from read to write.
        (True, "read", "write", False),
        (True, "write", "write", True),
        (True, "admin", "write", True),
    ],
)
@respx.mock(base_url=_BASE)
async def test_user_can_trigger_policy(
    respx_mock: respx.Router,
    private: bool,
    permission: str | None,
    required: str | None,
    expected: bool,
) -> None:
    """Given a privacy/permission pair, the trigger policy matches spec §4."""
    _mock_permission(respx_mock, permission)

    allowed = await _client().user_can_trigger(
        _REPO, private=private, user_login="alice", required=required
    )

    assert allowed is expected


@respx.mock(base_url=_BASE)
async def test_user_can_trigger_rejects_an_unknown_level(
    respx_mock: respx.Router,
) -> None:
    """Given a required level outside the literals, it fails before any call."""
    with pytest.raises(ValueError, match="required must be"):
        await _client().user_can_trigger(
            _REPO, private=False, user_login="alice", required="admin"
        )

    assert len(respx_mock.calls) == 0
