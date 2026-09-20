"""Behavior tests for the App-JWT install-flow operations (spec 01).

All GitHub traffic is faked with ``respx`` at ``https://api.github.com``; response
bodies come from :func:`test_github_helpers.fixture`, which derives a
schema-complete payload from the githubkit model so nothing is hand-maintained.
The App JWT is signed for real, so these tests also fail if the ``pyjwt[crypto]``
extra is dropped.
"""

from __future__ import annotations

import time
from typing import Any, cast

import httpx
import pytest
import respx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from githubkit_schemas.latest.models import (  # pyright: ignore[reportMissingTypeStubs]
    Enterprise,
    Installation,
    InstallationRepositoriesGetResponse200,
    InstallationToken,
    Integration,
)
from test_github_helpers import fixture

from slopolis_core.github.app_installations import MAX_REPO_PAGES, AppInstallations
from slopolis_core.github.errors import GitHubError, GitHubNotFoundError, GitHubRateLimitError
from slopolis_core.github.models import AppInstallation, InstallationRepository

_BASE = "https://api.github.com"
#: githubkit caches one JWT per issuer process-wide, so this App id is unique here.
_APP_ID = 5017301
_SLUG = "slopolis"
_INSTALLATION_ID = 42
_MISSING_INSTALLATION_ID = 404_404
_TOKEN = "ghs_installation_token"
_ACCESS_TOKENS = f"/app/installations/{_INSTALLATION_ID}/access_tokens"
_REPOSITORIES = "/installation/repositories"

#: One schema-complete repository entry, copied and re-keyed per page.
_REPO_TEMPLATE: dict[str, Any] = fixture(InstallationRepositoriesGetResponse200)["repositories"][0]


def _rsa_private_key() -> str:
    """A real RS256 key: every App-JWT request is signed with it."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return pem.decode()


_PRIVATE_KEY = _rsa_private_key()


def _installations() -> AppInstallations:
    """Build the App-JWT client; construction itself makes no request."""
    return AppInstallations(_APP_ID, _PRIVATE_KEY)


def _installation_body(**overrides: Any) -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "id": _INSTALLATION_ID,
        "account": {"login": "acme", "type": "Organization"},
        "target_type": "Organization",
        "repository_selection": "selected",
        "suspended_at": None,
    }
    return fixture(Installation, **{**defaults, **overrides})


def _repo_entry(
    github_id: int, *, private: bool = False, default_branch: str = "main"
) -> dict[str, Any]:
    return {
        **_REPO_TEMPLATE,
        "id": github_id,
        "full_name": f"acme/repo-{github_id}",
        "private": private,
        "default_branch": default_branch,
    }


def _repositories_body(entries: list[dict[str, Any]], *, total_count: int) -> dict[str, Any]:
    return {"total_count": total_count, "repositories": entries}


def _mock_token(mock: respx.Router) -> None:
    mock.post(_ACCESS_TOKENS).mock(
        return_value=httpx.Response(201, json=fixture(InstallationToken, token=_TOKEN))
    )


def _calls(route: respx.Route) -> list[Any]:
    """Return a route's recorded calls, typed for the assertions below."""
    return cast("list[Any]", list(route.calls))


@respx.mock(base_url=_BASE)
async def test_app_slug_returns_the_slug(respx_mock: respx.Router) -> None:
    """Given GET /app, app_slug returns the slug from an App-JWT request."""
    route = respx_mock.get("/app").mock(
        return_value=httpx.Response(200, json=fixture(Integration, slug=_SLUG))
    )
    installations = _installations()

    assert len(respx_mock.calls) == 0  # constructing the client makes no request
    slug = await installations.app_slug()

    assert slug == _SLUG
    assert route.calls.last.request.headers["authorization"].startswith("Bearer ")


@pytest.mark.parametrize(
    "body", [{}, {"slug": None}, {"slug": ""}], ids=["absent", "null", "empty"]
)
@respx.mock(base_url=_BASE)
async def test_app_slug_without_a_slug_raises(
    respx_mock: respx.Router, body: dict[str, Any]
) -> None:
    """Given a payload with no usable slug, app_slug raises GitHubError."""
    respx_mock.get("/app").mock(return_value=httpx.Response(200, json=fixture(Integration, **body)))

    with pytest.raises(GitHubError, match="has no slug"):
        await _installations().app_slug()


@respx.mock(base_url=_BASE)
async def test_get_installation_maps_the_installation(respx_mock: respx.Router) -> None:
    """Given an installation, every field maps from the account and target type."""
    respx_mock.get(f"/app/installations/{_INSTALLATION_ID}").mock(
        return_value=httpx.Response(200, json=_installation_body())
    )

    installation = await _installations().get_installation(_INSTALLATION_ID)

    assert installation == AppInstallation(
        installation_id=_INSTALLATION_ID,
        account_login="acme",
        account_type="Organization",
        repository_selection="selected",
        suspended=False,
    )


@respx.mock(base_url=_BASE)
async def test_get_installation_maps_a_suspended_enterprise_installation(
    respx_mock: respx.Router,
) -> None:
    """Given a suspended enterprise install, the login falls back to `name`."""
    body = _installation_body(
        target_type="Enterprise",
        repository_selection="all",
        suspended_at="2026-01-02T03:04:05Z",
    )
    # An Enterprise account carries no `login`; the mapping must fall back to `name`.
    body["account"] = fixture(Enterprise, name="Acme Industries")
    respx_mock.get(f"/app/installations/{_INSTALLATION_ID}").mock(
        return_value=httpx.Response(200, json=body)
    )

    installation = await _installations().get_installation(_INSTALLATION_ID)

    assert installation == AppInstallation(
        installation_id=_INSTALLATION_ID,
        account_login="Acme Industries",
        account_type="Enterprise",
        repository_selection="all",
        suspended=True,
    )


@respx.mock(base_url=_BASE)
async def test_get_installation_falls_back_to_the_account_type(respx_mock: respx.Router) -> None:
    """Given no target type, the account type identifies the installation."""
    respx_mock.get(f"/app/installations/{_INSTALLATION_ID}").mock(
        return_value=httpx.Response(
            200, json=_installation_body(target_type="", account={"type": "User"})
        )
    )

    installation = await _installations().get_installation(_INSTALLATION_ID)

    assert installation.account_type == "User"


@respx.mock(base_url=_BASE)
async def test_get_installation_404_raises_not_found(respx_mock: respx.Router) -> None:
    """Given an unknown installation id, GitHub's 404 is a typed not-found."""
    respx_mock.get(f"/app/installations/{_MISSING_INSTALLATION_ID}").mock(
        return_value=httpx.Response(404, json={"message": "Not Found"})
    )

    with pytest.raises(GitHubNotFoundError):
        await _installations().get_installation(_MISSING_INSTALLATION_ID)


@respx.mock(base_url=_BASE)
async def test_list_repositories_pages_through_every_repository(respx_mock: respx.Router) -> None:
    """Given 103 repositories over two pages, all map in order via the token."""
    _mock_token(respx_mock)
    pages = [
        _repositories_body([_repo_entry(i) for i in range(1, 101)], total_count=103),
        _repositories_body(
            [
                _repo_entry(101),
                _repo_entry(102, private=True),
                _repo_entry(103, private=True, default_branch=""),
            ],
            total_count=103,
        ),
    ]

    def route(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=pages[int(request.url.params["page"]) - 1])

    listing = respx_mock.get(_REPOSITORIES).mock(side_effect=route)

    repositories = await _installations().list_repositories(_INSTALLATION_ID)

    assert [repo.github_id for repo in repositories] == list(range(1, 104))
    assert repositories[0] == InstallationRepository(
        github_id=1, full_name="acme/repo-1", private=False, default_branch="main"
    )
    assert repositories[-1] == InstallationRepository(
        github_id=103, full_name="acme/repo-103", private=True, default_branch="main"
    )
    assert [call.request.url.params["page"] for call in _calls(listing)] == ["1", "2"]
    assert _calls(listing)[-1].request.headers["authorization"] == f"token {_TOKEN}"


@respx.mock(base_url=_BASE)
async def test_list_repositories_stops_at_the_page_cap(respx_mock: respx.Router) -> None:
    """Given pages that never end, the listing stops after MAX_REPO_PAGES."""
    _mock_token(respx_mock)

    def route(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        entries = [_repo_entry((page - 1) * 100 + i) for i in range(1, 101)]
        return httpx.Response(200, json=_repositories_body(entries, total_count=100_000))

    listing = respx_mock.get(_REPOSITORIES).mock(side_effect=route)

    repositories = await _installations().list_repositories(_INSTALLATION_ID)

    assert len(repositories) == MAX_REPO_PAGES * 100
    assert [call.request.url.params["page"] for call in _calls(listing)] == [
        str(page) for page in range(1, MAX_REPO_PAGES + 1)
    ]


@respx.mock(base_url=_BASE)
async def test_list_repositories_rate_limit_403_raises_rate_limit_error(
    respx_mock: respx.Router,
) -> None:
    """Given an exhausted rate limit, the 403 becomes a typed rate-limit error."""
    _mock_token(respx_mock)
    respx_mock.get(_REPOSITORIES).mock(
        return_value=httpx.Response(
            403,
            json={"message": "API rate limit exceeded for installation 42."},
            # githubkit retries a rate limit once, sleeping until the reset
            # (a zero or absent reset would make it sleep its 60s fallback).
            headers={
                "x-ratelimit-remaining": "0",
                "x-ratelimit-reset": str(int(time.time()) + 1),
            },
        )
    )

    with pytest.raises(GitHubRateLimitError):
        await _installations().list_repositories(_INSTALLATION_ID)
