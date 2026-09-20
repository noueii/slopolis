"""The per-installation GitHub client registry (spec 10.1).

The registry is built once at startup and handed out by the dependencies; these
tests fake GitHub at the same boundary the core client tests do (``respx`` on
``https://api.github.com``), so the installation-token mint, the shared token
cache, and the freshness of each client are the real ones.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx
from app.config import AppSettings
from app.main import create_app
from app.services.github_clients import InstallationClients
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from githubkit_schemas.latest.models import (  # pyright: ignore[reportMissingTypeStubs]
    InstallationToken,
)

from slopolis_core.github.app_installations import AppInstallations
from slopolis_core.settings import Settings

_BASE = "https://api.github.com"
#: githubkit caches one JWT per issuer process-wide, so this App id is unique here.
_APP_ID = 5017317
_OTHER_APP_ID = 5017319
_INSTALLATION_ID = 42
_OTHER_INSTALLATION_ID = 77
_TOKEN = "ghs_installation_token"
_EXPIRY = "2030-01-01T00:00:00Z"


def _rsa_private_key() -> str:
    """A real RS256 key: every installation-token mint is signed with it."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return pem.decode()


_PRIVATE_KEY = _rsa_private_key()


def _token_body() -> dict[str, Any]:
    """An installation-token response body: the fields GitHub actually sends."""
    body = InstallationToken(token=_TOKEN, expires_at=_EXPIRY)
    return body.model_dump(mode="json", exclude_unset=True)


def _core_settings(*, credentials: bool = True) -> Settings:
    """Core settings, optionally with App credentials, ignoring any .env on disk."""
    values: dict[str, Any] = {"APP_URL": "http://localhost:8400"}
    if credentials:
        values["GITHUB_APP_ID"] = str(_APP_ID)
        values["GITHUB_APP_PRIVATE_KEY"] = _PRIVATE_KEY
    return Settings.model_validate(values)


@respx.mock(base_url=_BASE)
async def test_client_for_reuses_the_minted_token_and_returns_fresh_clients(
    respx_mock: respx.Router,
) -> None:
    # Given an App registry and GitHub answering the token mint
    acme = respx_mock.post(f"/app/installations/{_INSTALLATION_ID}/access_tokens").mock(
        return_value=httpx.Response(200, json=_token_body())
    )
    widgets = respx_mock.post(
        f"/app/installations/{_OTHER_INSTALLATION_ID}/access_tokens"
    ).mock(return_value=httpx.Response(200, json=_token_body()))
    clients = InstallationClients(_APP_ID, _PRIVATE_KEY)

    # When three clients are requested for two installations, one of them twice
    first = await clients.client_for(_INSTALLATION_ID)
    second = await clients.client_for(_INSTALLATION_ID)
    elsewhere = await clients.client_for(_OTHER_INSTALLATION_ID)

    # Then each call is a fresh client, so one request's read cache and tool
    # budget never serve another...
    assert first is not second
    assert first.budget is not second.budget
    # ...reading through the installation it was asked for...
    assert first.installation_id == _INSTALLATION_ID
    assert second.installation_id == _INSTALLATION_ID
    assert elsewhere.installation_id == _OTHER_INSTALLATION_ID
    # ...over one token, minted once per installation and reused
    assert acme.call_count == 1
    assert widgets.call_count == 1


@respx.mock(base_url=_BASE)
async def test_booting_with_credentials_wires_the_registry_without_asking_github(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a deployment whose App credentials are configured
    monkeypatch.setattr("app.config.get_core_settings", _core_settings)
    monkeypatch.setattr("app.main.get_app_settings", AppSettings)
    app = create_app()

    # When the app starts
    async with app.router.lifespan_context(app):
        # Then both GitHub surfaces are ready — and nothing was asked of GitHub,
        # because no installation has to exist for the app to be configured
        assert isinstance(app.state.github_clients, InstallationClients)
        assert isinstance(app.state.app_installations, AppInstallations)
        assert len(respx.calls) == 0


@respx.mock(base_url=_BASE)
async def test_booting_without_credentials_leaves_github_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a deployment with no App credentials
    monkeypatch.setattr("app.config.get_core_settings", lambda: _core_settings(credentials=False))
    monkeypatch.setattr("app.main.get_app_settings", AppSettings)
    app = create_app()

    # When the app starts
    async with app.router.lifespan_context(app):
        # Then both surfaces are absent, the unconfigured-App path
        assert app.state.github_clients is None
        assert app.state.app_installations is None
