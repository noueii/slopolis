"""Behavior tests for the GitHub publisher (spec 10.7).

All GitHub traffic is faked with ``respx`` at ``https://api.github.com``; response
bodies come from :func:`test_github_helpers.fixture`, which derives a
schema-complete payload from the githubkit model so nothing is hand-maintained.
"""

from __future__ import annotations

import json
from typing import Any, TypeGuard

import httpx
import pytest
import respx
from githubkit import GitHub, TokenAuthStrategy
from githubkit_schemas.latest.models import (  # pyright: ignore[reportMissingTypeStubs]
    CheckRun,
    Integration,
    IssueComment,
    PullRequestReviewComment,
    ReposOwnerRepoCommitsRefCheckRunsGetResponse200,
)
from test_github_helpers import fixture

from slopolis_core.github.errors import GitHubError
from slopolis_core.github.models import InlineComment
from slopolis_core.github.publisher import CHECK_RUN_NAME, SUMMARY_MARKER, GitHubPublisher

_BASE = "https://api.github.com"
_REPO = "acme/widget"
_OWNER, _NAME = _REPO.split("/")
_TOKEN = "ghs_test"
_COMMIT = "headsha"
_NUMBER = 7
_APP_ID = 987
_OTHER_APP_ID = 654
_COMMENTS_URL = f"/repos/{_OWNER}/{_NAME}/issues/{_NUMBER}/comments"


def _publisher(*, app_id: int | None = _APP_ID) -> GitHubPublisher:
    return GitHubPublisher(GitHub(TokenAuthStrategy(_TOKEN)), app_id=app_id)


def _comment(comment_id: int, *, body: str, app_id: int | None = None) -> dict[str, Any]:
    """One comment body as GitHub serves it: performed by ``app_id``'s App, or a person."""
    if app_id is None:
        return fixture(IssueComment, id=comment_id, body=body)
    return fixture(
        IssueComment,
        id=comment_id,
        body=body,
        performed_via_github_app=fixture(Integration, id=app_id),
    )


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


@respx.mock(base_url=_BASE)
async def test_find_summary_comment_ignores_a_marker_written_by_someone_else(
    respx_mock: respx.Router,
) -> None:
    """A quoted marker from a user, or another App, is not slopolis's to edit."""
    respx_mock.get(_COMMENTS_URL).mock(
        return_value=httpx.Response(
            200,
            json=[
                _comment(11, body=f">{SUMMARY_MARKER}\n\nsomeone's reply, quoting the review"),
                _comment(12, body=f"{SUMMARY_MARKER}\n\n- Status: **done**", app_id=_OTHER_APP_ID),
                _comment(13, body=f"{SUMMARY_MARKER}\n\n- Status: **done**", app_id=_APP_ID),
            ],
        )
    )

    found = await _publisher().find_summary_comment(_REPO, _NUMBER)

    assert found == 13


@respx.mock(base_url=_BASE)
async def test_find_summary_comment_scans_past_a_full_page(respx_mock: respx.Router) -> None:
    """A summary behind a full page of conversation is still found."""
    route = respx_mock.get(_COMMENTS_URL).mock(
        side_effect=[
            httpx.Response(
                200, json=[_comment(1000 + index, body="chatter") for index in range(100)]
            ),
            httpx.Response(200, json=[_comment(21, body=SUMMARY_MARKER, app_id=_APP_ID)]),
        ]
    )

    found = await _publisher().find_summary_comment(_REPO, _NUMBER)

    assert found == 21
    assert route.call_count == 2


@respx.mock(base_url=_BASE)
async def test_find_summary_comment_answers_none_without_a_marker(
    respx_mock: respx.Router,
) -> None:
    """A pull request that never had a review has no comment to edit."""
    route = respx_mock.get(_COMMENTS_URL).mock(
        return_value=httpx.Response(200, json=[_comment(31, body="please review this")])
    )

    found = await _publisher().find_summary_comment(_REPO, _NUMBER)

    assert found is None
    # The short page ends the walk: a bounded lookup is one request here, not five
    assert route.call_count == 1


@respx.mock(base_url=_BASE)
async def test_find_summary_comment_matches_an_app_comment_when_the_app_is_unknown(
    respx_mock: respx.Router,
) -> None:
    """A publisher that cannot name its own App still rolls its comment forward."""
    respx_mock.get(_COMMENTS_URL).mock(
        return_value=httpx.Response(
            200,
            json=[
                _comment(41, body=f">{SUMMARY_MARKER}\n\nquoting the review"),
                _comment(42, body=SUMMARY_MARKER, app_id=_OTHER_APP_ID),
            ],
        )
    )

    found = await _publisher(app_id=None).find_summary_comment(_REPO, _NUMBER)

    assert found == 42


@respx.mock(base_url=_BASE)
async def test_find_summary_comment_reports_a_refused_listing(respx_mock: respx.Router) -> None:
    """A listing GitHub refuses surfaces as a ``GitHubError`` for the caller's fallback."""
    respx_mock.get(_COMMENTS_URL).mock(return_value=httpx.Response(403, json={"message": "nope"}))

    with pytest.raises(GitHubError):
        await _publisher().find_summary_comment(_REPO, _NUMBER)
