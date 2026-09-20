"""Behavior tests for the worker's GitHub client factories (spec 10.5, 10.7).

The publisher's own identity has tests of its own because nothing else covers it:
it is what reconciliation matches this App's review comments by, and a review
comment — unlike an issue comment — carries no ``performed_via_github_app`` field
to match instead. The factories delegate that resolution to ``_app_login``, which
is what these tests exercise directly; GitHub traffic is faked with ``respx``.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator

import httpx
import pytest
import respx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from worker.deps import _app_login  # pyright: ignore[reportPrivateUsage]

from slopolis_core.settings import get_settings

_BASE = "https://api.github.com"
_APP_ID = "5007508"
_SLUG = "slopolis-dev"


def _rsa_private_key() -> str:
    """A real RS256 key: an App-JWT request is signed with it before it is sent."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return pem.decode()


@pytest.fixture
def settings_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """An environment with no GitHub App, so each test adds only what it needs."""
    for name in ("GITHUB_APP_SLUG", "GITHUB_APP_ID", "GITHUB_APP_PRIVATE_KEY"):
        monkeypatch.setenv(name, "")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@respx.mock(base_url=_BASE)
async def test_a_configured_slug_names_the_login_this_app_writes_as(
    respx_mock: respx.Router, settings_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A slug in the environment is the App's bot login, and costs no request."""
    # Given a deployment that configured the App's slug, and no mocked route: a
    # request from here would fail the test instead of answering
    monkeypatch.setenv("GITHUB_APP_SLUG", _SLUG)

    # When the worker resolves the login its comments carry
    login = await _app_login(get_settings())

    # Then it is the bot account GitHub attributes an App's review comment to
    assert login == f"{_SLUG}[bot]"
    assert respx_mock.calls == []


async def test_without_app_credentials_there_is_no_login(settings_env: None) -> None:
    """A worker that cannot name its App knows of no login rather than guessing one."""
    assert await _app_login(get_settings()) is None


@respx.mock(base_url=_BASE)
async def test_a_slug_lookup_github_refuses_still_leaves_a_publisher(
    respx_mock: respx.Router,
    settings_env: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A refused slug read costs the identity, never the review it was reading for."""
    # Given an App whose credentials are configured but whose slug is not, and a
    # GitHub that refuses to answer
    monkeypatch.setenv("GITHUB_APP_ID", _APP_ID)
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY", _rsa_private_key())
    route = respx_mock.get(f"{_BASE}/app").mock(
        return_value=httpx.Response(403, json={"message": "nope"})
    )

    # When the worker resolves the login its comments carry
    with caplog.at_level(logging.WARNING, logger="worker.deps"):
        login = await _app_login(get_settings())

    # Then publishing continues with the login unknown, and the reason is logged
    assert route.called
    assert login is None
    assert any(
        "could not resolve the App's slug" in record.getMessage() for record in caplog.records
    )
