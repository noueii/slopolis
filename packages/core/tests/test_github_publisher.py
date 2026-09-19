"""Behavior tests for the GitHub publisher (spec 10.7).

All GitHub traffic is faked with ``respx`` at ``https://api.github.com``; response
bodies come from :func:`test_github_helpers.fixture`, which derives a
schema-complete payload from the githubkit model so nothing is hand-maintained.
"""

from __future__ import annotations

import json
from typing import Any, TypeGuard

import httpx
import respx
from githubkit import GitHub, TokenAuthStrategy
from githubkit_schemas.latest.models import (  # pyright: ignore[reportMissingTypeStubs]
    CheckRun,
    IssueComment,
    PullRequestReviewComment,
    ReposOwnerRepoCommitsRefCheckRunsGetResponse200,
)
from test_github_helpers import fixture

from slopolis_core.github.models import InlineComment
from slopolis_core.github.publisher import CHECK_RUN_NAME, GitHubPublisher

_BASE = "https://api.github.com"
_REPO = "acme/widget"
_OWNER, _NAME = _REPO.split("/")
_TOKEN = "ghs_test"
_COMMIT = "headsha"
_NUMBER = 7


def _publisher() -> GitHubPublisher:
    return GitHubPublisher(GitHub(TokenAuthStrategy(_TOKEN)))


def _is_str_dict(value: object) -> TypeGuard[dict[str, Any]]:
    """Narrow ``value`` to a string-keyed dict (isinstance narrows to Unknown)."""
    return isinstance(value, dict)


def _request_body(request: httpx.Request) -> dict[str, Any]:
    payload: Any = json.loads(request.content)
    assert _is_str_dict(payload)
    return payload


@respx.mock(base_url=_BASE)
async def test_upsert_summary_comment_creates_when_no_existing(respx_mock: respx.Router) -> None:
    """Given no existing id, the publisher POSTs a new comment and returns its id."""
    body = fixture(IssueComment, id=101)
    route = respx_mock.post(f"/repos/{_OWNER}/{_NAME}/issues/{_NUMBER}/comments").mock(
        return_value=httpx.Response(201, json=body)
    )

    returned = await _publisher().upsert_summary_comment(
        _REPO, _NUMBER, "summary body", existing_comment_id=None
    )

    assert returned == 101
    assert route.called
    assert _request_body(route.calls.last.request) == {"body": "summary body"}


@respx.mock(base_url=_BASE)
async def test_upsert_summary_comment_edits_when_id_supplied(respx_mock: respx.Router) -> None:
    """Given an existing id, the publisher PATCHes that comment and returns its id."""
    body = fixture(IssueComment, id=42)
    route = respx_mock.patch(f"/repos/{_OWNER}/{_NAME}/issues/comments/42").mock(
        return_value=httpx.Response(200, json=body)
    )

    returned = await _publisher().upsert_summary_comment(
        _REPO, _NUMBER, "edited body", existing_comment_id=42
    )

    assert returned == 42
    assert route.called
    assert _request_body(route.calls.last.request) == {"body": "edited body"}


@respx.mock(base_url=_BASE)
async def test_post_inline_comments_payload_and_ids(respx_mock: respx.Router) -> None:
    """Given two findings, the publisher posts each with the PR anchor and returns ids."""
    bodies = [fixture(PullRequestReviewComment, id=i) for i in (201, 202)]
    sent: list[dict[str, Any]] = []

    def route(request: httpx.Request) -> httpx.Response:
        sent.append(_request_body(request))
        return httpx.Response(201, json=bodies[len(sent) - 1])

    mocked = respx_mock.post(f"/repos/{_OWNER}/{_NAME}/pulls/{_NUMBER}/comments").mock(
        side_effect=route
    )
    comments = [
        InlineComment(path="a.py", line=3, body="first"),
        InlineComment(path="b.py", line=9, body="second"),
    ]

    ids = await _publisher().post_inline_comments(_REPO, _NUMBER, comments, _COMMIT)

    assert ids == [201, 202]
    assert mocked.call_count == 2
    assert sent[0] == {
        "body": "first",
        "path": "a.py",
        "line": 3,
        "side": "RIGHT",
        "commit_id": _COMMIT,
    }
    assert sent[1] == {
        "body": "second",
        "path": "b.py",
        "line": 9,
        "side": "RIGHT",
        "commit_id": _COMMIT,
    }


@respx.mock(base_url=_BASE)
async def test_upsert_check_run_creates_with_conclusion(respx_mock: respx.Router) -> None:
    """Given no existing run, the publisher creates one, maps the conclusion, returns id."""
    respx_mock.get(f"/repos/{_OWNER}/{_NAME}/commits/{_COMMIT}/check-runs").mock(
        return_value=httpx.Response(
            200,
            json=fixture(
                ReposOwnerRepoCommitsRefCheckRunsGetResponse200,
                total_count=0,
                check_runs=[],
            ),
        )
    )
    route = respx_mock.post(f"/repos/{_OWNER}/{_NAME}/check-runs").mock(
        return_value=httpx.Response(201, json=fixture(CheckRun, id=303))
    )

    returned = await _publisher().upsert_check_run(
        _REPO, _COMMIT, conclusion="failure", title="Review", summary="2 findings"
    )

    assert returned == 303
    assert route.called
    sent = _request_body(route.calls.last.request)
    assert sent["conclusion"] == "failure"
    assert sent["name"] == CHECK_RUN_NAME
    assert sent["head_sha"] == _COMMIT
    assert sent["status"] == "completed"
    assert sent["output"] == {"title": "Review", "summary": "2 findings"}


@respx.mock(base_url=_BASE)
async def test_upsert_check_run_updates_existing(respx_mock: respx.Router) -> None:
    """Given an existing run, the publisher PATCHes it and returns its id."""
    respx_mock.get(f"/repos/{_OWNER}/{_NAME}/commits/{_COMMIT}/check-runs").mock(
        return_value=httpx.Response(
            200,
            json=fixture(
                ReposOwnerRepoCommitsRefCheckRunsGetResponse200,
                total_count=1,
                check_runs=[fixture(CheckRun, id=777, name=CHECK_RUN_NAME)],
            ),
        )
    )
    route = respx_mock.patch(f"/repos/{_OWNER}/{_NAME}/check-runs/777").mock(
        return_value=httpx.Response(200, json=fixture(CheckRun, id=777))
    )

    returned = await _publisher().upsert_check_run(
        _REPO, _COMMIT, conclusion="success", title="Review", summary="clean"
    )

    assert returned == 777
    assert route.called
    assert _request_body(route.calls.last.request)["conclusion"] == "success"
