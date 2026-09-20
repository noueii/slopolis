"""The pre-flight live check runs on the workspace's own credential (spec 10.2).

The screen's *Test connection* and a submission's live check must reach the same
provider, so these tests drive the check the way pre-flight does — a model
resolved for the review role, the credential linked to that model, the base URL
and key the call actually used — with a recording factory standing in for the
HTTP client, so no test speaks to a provider.
"""

from __future__ import annotations

import base64
from collections.abc import Iterator
from typing import Any

import pytest
from app.adapters.workspace import WorkspaceConfigAdapter
from app.deps import get_vault
from app.main import create_app
from app.services.live_check import (
    CredentialClientPool,
    ModelCredential,
    WorkspaceLiveModelCheck,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from slopolis_core.github.errors import GitHubNotFoundError
from slopolis_core.github.models import GitHubPullRequest, InstallationRepository
from slopolis_core.llm.client import LlmClient, LlmError
from slopolis_core.llm.models import ChatMessage, CompletionResult
from slopolis_core.preflight.models import PreflightOutcome, PreflightRequest
from slopolis_core.preflight.service import PreflightService
from slopolis_core.settings import get_settings
from slopolis_core.vault import SecretVault, VaultDecryptError
from slopolis_db.models import ModelCatalog, ProviderCredential

from .conftest import (
    ApiHarness,
    FakeGateway,
    FakeInstallationClients,
    make_ref,
    seed_review_model,
)

_MODEL = "claude-sonnet-4"
_URL = "https://github.com/acme/api/pull/11"
_PROVIDER_URL = "https://byok.example"
_GATEWAY_URL = "http://gateway.example"
_API_KEY = "sk-live-abcdefgh"
_MASTER_KEY = base64.b64encode(b"live-check-tests-master-key-material").decode()


def _vault(material: str = "live-check-tests-master-key-material") -> SecretVault:
    """A vault the tests seal and open a credential's key with."""
    return SecretVault.from_env(material)


def _credential(
    api_key: str = _API_KEY, *, base_url: str = _PROVIDER_URL
) -> ModelCredential:
    """The credential a model resolves to, as the adapter reports it."""
    return ModelCredential(base_url=base_url, api_key=api_key, key_last4=api_key[-4:])


def _result(model: str) -> CompletionResult:
    """A one-token completion answer, which is all the check reads."""
    return CompletionResult(
        text="pong",
        model=model,
        provider="litellm",
        prompt_tokens=1,
        completion_tokens=1,
        total_tokens=2,
        cost_usd=0.0,
    )


class RecordingClient:
    """A chat client that records what it was asked and whether it was closed."""

    def __init__(self, base_url: str, api_key: str) -> None:
        self.base_url = base_url
        self.api_key = api_key
        self.calls: list[tuple[str, int | None]] = []
        self.closed = False

    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        model: str,
        max_tokens: int | None = None,
        temperature: float = 0.0,
    ) -> CompletionResult:
        self.calls.append((model, max_tokens))
        return _result(model)

    async def aclose(self) -> None:
        self.closed = True


class RecordingFactory:
    """Stands in for ``LiteLlmClient``: records the credentials, speaks no HTTP."""

    def __init__(self) -> None:
        self.built: list[tuple[str, str]] = []
        self.clients: list[RecordingClient] = []

    def __call__(self, base_url: str, api_key: str) -> RecordingClient:
        self.built.append((base_url, api_key))
        client = RecordingClient(base_url, api_key)
        self.clients.append(client)
        return client


class GatewayClient:
    """The process-level gateway the app opened at boot: records, no HTTP."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        model: str,
        max_tokens: int | None = None,
        temperature: float = 0.0,
    ) -> CompletionResult:
        self.calls.append(model)
        return _result(model)


class FakeCredentialSource:
    """``ModelCredentialSource`` for one workspace: a credential, or a failure."""

    def __init__(
        self,
        credential: ModelCredential | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self.credential = credential
        self._error = error
        self.asked: list[str] = []

    async def credential_for_model(self, model_id: str) -> ModelCredential | None:
        self.asked.append(model_id)
        if self._error is not None:
            raise self._error
        return self.credential


def _check(
    source: FakeCredentialSource,
    factory: RecordingFactory,
    *,
    fallback: LlmClient | None = None,
) -> WorkspaceLiveModelCheck:
    """A check over a fake workspace, pooling clients from ``factory``."""
    return WorkspaceLiveModelCheck(
        source, clients=CredentialClientPool(factory), fallback=fallback
    )


# --- the check itself -------------------------------------------------------


async def test_the_check_runs_the_token_on_the_models_own_credential() -> None:
    # Given a workspace whose model is served by a stored credential
    source = FakeCredentialSource(_credential())
    factory = RecordingFactory()

    # When the live check runs for that model
    await _check(source, factory).check(_MODEL)

    # Then the completion went to the credential's base URL with the credential's
    # key, for the model the check was asked about, and cost one token
    assert source.asked == [_MODEL]
    assert factory.built == [(_PROVIDER_URL, _API_KEY)]
    assert factory.clients[0].calls == [(_MODEL, 1)]


async def test_the_check_reuses_one_client_per_credential() -> None:
    # Given a check that already ran once for a credential
    factory = RecordingFactory()
    check = _check(FakeCredentialSource(_credential()), factory)
    await check.check(_MODEL)

    # When the next submission checks the same model
    await check.check(_MODEL)

    # Then the pooled client was reused rather than a second one built, so the
    # submission does not pay for a new connection pool every time
    assert len(factory.built) == 1
    assert len(factory.clients[0].calls) == 2


async def test_a_rotated_key_is_a_new_client_even_with_the_same_last_four() -> None:
    # Given two keys that share their last four characters, i.e. what the screen
    # shows and what the pool could confuse them by
    source = FakeCredentialSource(_credential("sk-live-abcdefgh"))
    factory = RecordingFactory()
    check = _check(source, factory)
    await check.check(_MODEL)

    # When the credential is rotated to the other one
    source.credential = _credential("sk-live-wxyzefgh")
    await check.check(_MODEL)

    # Then the call used a client built from the new key, not the revoked one
    assert factory.built == [
        (_PROVIDER_URL, "sk-live-abcdefgh"),
        (_PROVIDER_URL, "sk-live-wxyzefgh"),
    ]


async def test_the_pool_keeps_a_bound_and_closes_what_it_drops() -> None:
    # Given a pool that holds two clients
    factory = RecordingFactory()
    pool = CredentialClientPool(factory, max_size=2)
    await pool.client_for(_credential("sk-one-1111", base_url="https://one.example"))
    await pool.client_for(_credential("sk-two-2222", base_url="https://two.example"))

    # When a third credential is called
    await pool.client_for(_credential("sk-three-3333", base_url="https://three.example"))

    # Then the oldest client was closed and dropped, so a process that sees many
    # credentials cannot grow its sockets without limit
    assert [client.closed for client in factory.clients] == [True, False, False]


async def test_the_pool_closes_every_client_at_shutdown() -> None:
    # Given a pool holding a client per credential
    factory = RecordingFactory()
    pool = CredentialClientPool(factory)
    await pool.client_for(_credential("sk-one-1111", base_url="https://one.example"))
    await pool.client_for(_credential("sk-two-2222", base_url="https://two.example"))

    # When the app shuts the pool down
    await pool.aclose()

    # Then nothing was left open
    assert [client.closed for client in factory.clients] == [True, True]


async def test_without_a_credential_the_process_gateway_answers() -> None:
    # Given a workspace whose model no credential serves, and a deployment whose
    # gateway is configured
    factory = RecordingFactory()
    gateway = GatewayClient()

    # When the live check runs
    await _check(FakeCredentialSource(None), factory, fallback=gateway).check(_MODEL)

    # Then the shared gateway made the call and no per-credential client was built
    assert gateway.calls == [_MODEL]
    assert factory.built == []


async def test_with_neither_is_a_failure_naming_the_model_and_both_ways_out() -> None:
    # Given a workspace with no usable credential and no gateway
    check = _check(FakeCredentialSource(None), RecordingFactory())

    # When the live check runs
    with pytest.raises(LlmError) as raised:
        await check.check(_MODEL)

    # Then the failure names the model and both fixes, because neither path is a
    # silent default
    message = str(raised.value)
    assert _MODEL in message
    assert "link a credential to" in message
    assert "LITELLM_BASE_URL" in message
    assert "LITELLM_MASTER_KEY" in message


async def test_a_key_this_process_cannot_open_fails_the_live_check() -> None:
    # Given a credential sealed under a master key this process does not have
    source = FakeCredentialSource(
        error=VaultDecryptError("the secret could not be decrypted")
    )

    # When the live check runs
    with pytest.raises(LlmError) as raised:
        await _check(source, RecordingFactory()).check(_MODEL)

    # Then it fails as a configuration problem naming the model, not as an
    # absent credential that would quietly fall back to a different provider
    message = str(raised.value)
    assert _MODEL in message
    assert "cannot be decrypted" in message
    assert "ENCRYPTION_KEY" in message


# --- which credential the workspace reports ---------------------------------


async def test_the_adapter_answers_the_credential_linked_to_a_model(
    seeded: Any, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    # Given a model whose catalog row is linked to an enabled credential
    vault = _vault()
    async with session_factory() as session:
        await seed_review_model(
            session,
            seeded.workspace_id,
            vault=vault,
            api_key=_API_KEY,
            base_url=_PROVIDER_URL,
        )
        adapter = WorkspaceConfigAdapter(session, seeded.workspace_id, vault=vault)

        # When the credential is asked for, for that model and for another
        found = await adapter.credential_for_model(_MODEL)
        unknown = await adapter.credential_for_model("not-in-this-catalog")

    # Then the key came back decrypted for exactly that model
    assert found == _credential()
    assert unknown is None


async def test_the_adapter_treats_a_disabled_credential_as_no_credential(
    seeded: Any, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    # Given a model linked to a credential the workspace has switched off
    vault = _vault()
    async with session_factory() as session:
        credential = await seed_review_model(
            session,
            seeded.workspace_id,
            vault=vault,
            api_key=_API_KEY,
            base_url=_PROVIDER_URL,
            enabled=False,
        )
        adapter = WorkspaceConfigAdapter(session, seeded.workspace_id, vault=vault)
        disabled = await adapter.credential_for_model(_MODEL)

        # When the workspace re-enables it
        credential.enabled = True
        await session.flush()
        enabled = await adapter.credential_for_model(_MODEL)

    # Then a disabled link is no link at all, and re-enabling restores the call
    assert disabled is None
    assert enabled == _credential()


async def test_the_adapter_uses_the_deployments_base_url_when_the_credential_has_none(
    seeded: Any, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    # Given a credential that stores no base URL of its own
    vault = _vault()
    async with session_factory() as session:
        await seed_review_model(
            session, seeded.workspace_id, vault=vault, api_key=_API_KEY
        )
        bare = WorkspaceConfigAdapter(session, seeded.workspace_id, vault=vault)
        without_default = await bare.credential_for_model(_MODEL)

        # When the deployment does configure a gateway base URL
        configured = WorkspaceConfigAdapter(
            session, seeded.workspace_id, vault=vault, default_base_url=_GATEWAY_URL
        )
        with_default = await configured.credential_for_model(_MODEL)

    # Then the credential runs on the URL the connection probe would have tested,
    # and with no base URL anywhere it is simply unusable
    assert without_default is None
    assert with_default == _credential(base_url=_GATEWAY_URL)


async def test_the_adapter_has_no_credential_for_an_unlinked_model_or_without_a_vault(
    seeded: Any, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    # Given a model the catalog links to no credential, and a process with a vault
    vault = _vault()
    async with session_factory() as session:
        credential = await seed_review_model(
            session,
            seeded.workspace_id,
            vault=vault,
            api_key=_API_KEY,
            base_url=_PROVIDER_URL,
            link=False,
        )
        adapter = WorkspaceConfigAdapter(session, seeded.workspace_id, vault=vault)
        unlinked = await adapter.credential_for_model(_MODEL)

        # When the link is recorded but the process has no ENCRYPTION_KEY
        catalog = await session.scalar(
            select(ModelCatalog).where(ModelCatalog.workspace_id == seeded.workspace_id)
        )
        assert catalog is not None
        catalog.credential_id = credential.id
        await session.flush()
        no_vault = WorkspaceConfigAdapter(session, seeded.workspace_id)
        unreadable = await no_vault.credential_for_model(_MODEL)
        readable = await adapter.credential_for_model(_MODEL)

    # Then neither a model nothing links to nor a process that cannot open a key
    # has a usable credential — and the link itself was the only thing missing
    assert unlinked is None
    assert unreadable is None
    assert readable == _credential()


# --- pre-flight, end to end through the service -----------------------------


async def _run_preflight(
    adapter: WorkspaceConfigAdapter,
    *,
    factory: RecordingFactory,
    fallback: LlmClient | None,
) -> PreflightOutcome:
    """Run pre-flight over one covered link with the real adapter and check."""
    service = PreflightService(
        FakeGateway(refs={_URL: make_ref("acme/api", 11)}),
        adapter,
        WorkspaceLiveModelCheck(
            adapter, clients=CredentialClientPool(factory), fallback=fallback
        ),
    )
    return await service.run(PreflightRequest(pr_urls=[_URL]), user_login="octocat")


async def test_preflight_checks_the_linked_credential_not_the_gateway(
    seeded: Any, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    # Given a workspace whose review model is served by its own credential, and
    # a deployment that also has a shared gateway
    vault = _vault()
    async with session_factory() as session:
        await seed_review_model(
            session,
            seeded.workspace_id,
            vault=vault,
            api_key=_API_KEY,
            base_url=_PROVIDER_URL,
        )
        adapter = WorkspaceConfigAdapter(
            session, seeded.workspace_id, vault=vault, default_base_url=_GATEWAY_URL
        )
        factory = RecordingFactory()
        gateway = GatewayClient()

        # When a link is pre-flighted
        outcome = await _run_preflight(adapter, factory=factory, fallback=gateway)

    # Then the model call ran on the credential and the link is valid...
    assert [ref.url for ref in outcome.valid] == [_URL]
    assert outcome.notices == []
    assert factory.built == [(_PROVIDER_URL, _API_KEY)]
    assert factory.clients[0].calls == [(_MODEL, 1)]
    # ...not on the gateway it merely could have used
    assert gateway.calls == []


async def test_preflight_falls_back_to_the_gateway_for_a_disabled_link(
    seeded: Any, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    # Given a review model whose linked credential is switched off, and another
    # credential the workspace still holds (so it counts as configured)
    vault = _vault()
    async with session_factory() as session:
        await seed_review_model(
            session,
            seeded.workspace_id,
            vault=vault,
            api_key=_API_KEY,
            base_url=_PROVIDER_URL,
            enabled=False,
        )
        session.add(
            ProviderCredential(
                workspace_id=seeded.workspace_id,
                provider="OpenAI",
                encrypted_api_key=vault.seal("sk-other-0000"),
                key_last4="0000",
                enabled=True,
            )
        )
        await session.commit()

    # When a link is pre-flighted
    async with session_factory() as session:
        adapter = WorkspaceConfigAdapter(session, seeded.workspace_id, vault=vault)
        factory = RecordingFactory()
        gateway = GatewayClient()
        outcome = await _run_preflight(adapter, factory=factory, fallback=gateway)

    # Then the disabled credential is treated as none: the shared gateway answers
    assert [ref.url for ref in outcome.valid] == [_URL]
    assert gateway.calls == [_MODEL]
    assert factory.built == []


async def test_preflight_names_both_ways_out_when_nothing_can_call_the_model(
    seeded: Any, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    # Given a workspace with a credential it cannot open and a deployment with no
    # gateway: neither of the two ways to reach a model exists
    async with session_factory() as session:
        await seed_review_model(session, seeded.workspace_id)
        adapter = WorkspaceConfigAdapter(session, seeded.workspace_id)
        outcome = await _run_preflight(
            adapter, factory=RecordingFactory(), fallback=None
        )

    # Then the notice names the model and both fixes (spec 10.2)
    assert any(
        _MODEL in notice
        and "link a credential to" in notice
        and "LITELLM_MASTER_KEY" in notice
        for notice in outcome.notices
    )


# --- the route's own assembly ----------------------------------------------


class PreflightGitHub:
    """The GitHub read surface the real gateway needs, for one covered PR.

    These tests are about the model call, so GitHub answers exactly what
    pre-flight must get past to reach it: the repository is covered, the link
    resolves, the viewer may trigger, and ``.codereview.yml`` is absent.
    """

    async def list_installation_repositories(self) -> list[InstallationRepository]:
        return [
            InstallationRepository(
                github_id=4242,
                full_name="acme/api",
                private=False,
                default_branch="main",
            )
        ]

    async def resolve_pr(self, url: str) -> GitHubPullRequest:
        return GitHubPullRequest(
            repo_full_name="acme/api",
            private=False,
            number=11,
            title="fix: guard token refresh skew",
            url=url,
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

    async def user_can_trigger(
        self,
        repo_full_name: str,
        *,
        private: bool,
        user_login: str,
        required: str | None = None,
    ) -> bool:
        return True

    async def read_file(self, repo_full_name: str, path: str, ref: str) -> str:
        raise GitHubNotFoundError(f"{path} is not in {repo_full_name}")


@pytest.fixture
def vault_key(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Give one test a vault key, leaving the settings/vault memos clean behind it."""
    monkeypatch.setenv("ENCRYPTION_KEY", _MASTER_KEY)
    _reset_vault()
    yield
    _reset_vault()


@pytest.fixture
def no_vault(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Give one test a process with no ``ENCRYPTION_KEY``, ambient env or not."""
    monkeypatch.delenv("ENCRYPTION_KEY", raising=False)
    _reset_vault()
    yield
    _reset_vault()


def _reset_vault() -> None:
    """Clear the cached settings and vault, so the next caller re-reads the env."""
    get_settings.cache_clear()
    get_vault.cache_clear()


async def test_the_preflight_route_calls_the_credentials_endpoint(
    seeded: Any,
    session_factory: async_sessionmaker[AsyncSession],
    build_harness: Any,
    vault_key: None,
) -> None:
    # Given a workspace whose review model is served by a credential sealed under
    # this process's ENCRYPTION_KEY, and a deployment with no gateway at all
    vault = _vault()
    async with session_factory() as session:
        await seed_review_model(
            session,
            seeded.workspace_id,
            vault=vault,
            api_key=_API_KEY,
            base_url=_PROVIDER_URL,
        )
    factory = RecordingFactory()
    harness: ApiHarness = await build_harness(
        user_id=seeded.user_id,
        real_preflight=True,
        unconfigured_gateway=True,
        github_clients=FakeInstallationClients({555: PreflightGitHub()}),
        live_check_clients=CredentialClientPool(factory),
    )

    # When a link is pre-flighted through the real dependency assembly
    response = await harness.client.post(
        "/api/reviews/preflight", json={"prUrls": [_URL]}
    )

    # Then the check ran on the credential's endpoint with the credential's key
    assert response.status_code == 200
    body = response.json()
    assert [item["url"] for item in body["valid"]] == [_URL]
    assert factory.built == [(_PROVIDER_URL, _API_KEY)]
    assert factory.clients[0].calls == [(_MODEL, 1)]
    # ...and the key is nowhere in what the user is shown
    assert _API_KEY not in response.text


async def test_the_preflight_route_falls_back_to_the_process_gateway(
    seeded: Any,
    session_factory: async_sessionmaker[AsyncSession],
    build_harness: Any,
    no_vault: None,
) -> None:
    # Given a deployment with a gateway and no way to open the stored credential
    # (no ENCRYPTION_KEY in this process), whose model therefore has no usable one
    async with session_factory() as session:
        await seed_review_model(session, seeded.workspace_id)
    factory = RecordingFactory()
    gateway = GatewayClient()
    harness: ApiHarness = await build_harness(
        user_id=seeded.user_id,
        real_preflight=True,
        unconfigured_gateway=True,
        llm_client=gateway,
        github_clients=FakeInstallationClients({555: PreflightGitHub()}),
        live_check_clients=CredentialClientPool(factory),
    )

    # When a link is pre-flighted through the real dependency assembly
    response = await harness.client.post(
        "/api/reviews/preflight", json={"prUrls": [_URL]}
    )

    # Then the shared gateway the app opened at boot answered, as the spec's
    # fallback says, and no per-credential client was built
    assert response.status_code == 200
    assert [item["url"] for item in response.json()["valid"]] == [_URL]
    assert gateway.calls == [_MODEL]
    assert factory.built == []


async def test_the_preflight_route_reports_an_undecryptable_credential_as_a_notice(
    seeded: Any,
    session_factory: async_sessionmaker[AsyncSession],
    build_harness: Any,
    vault_key: None,
) -> None:
    # Given a credential sealed under a key that is not this process's — a
    # rotated ENCRYPTION_KEY — and no gateway to fall back on
    async with session_factory() as session:
        await seed_review_model(
            session,
            seeded.workspace_id,
            vault=_vault("another-process-master-key-material"),
            api_key=_API_KEY,
            base_url=_PROVIDER_URL,
        )
    factory = RecordingFactory()
    harness: ApiHarness = await build_harness(
        user_id=seeded.user_id,
        real_preflight=True,
        unconfigured_gateway=True,
        github_clients=FakeInstallationClients({555: PreflightGitHub()}),
        live_check_clients=CredentialClientPool(factory),
    )

    # When a link is pre-flighted
    response = await harness.client.post(
        "/api/reviews/preflight", json={"prUrls": [_URL]}
    )

    # Then it is a validation notice naming the model — the same notice path as
    # any other live-check failure, not a 500 and not a silent "no credential"
    assert response.status_code == 200
    body = response.json()
    assert body["valid"] == []
    assert body["invalid"] == [_URL]
    assert any(
        _MODEL in notice and "cannot be decrypted" in notice
        for notice in body["notices"]
    )
    assert factory.built == []
    assert _API_KEY not in response.text


async def test_the_lifespan_closes_the_clients_the_app_pooled() -> None:
    # Given a client the live check pooled while the app was running
    app = create_app()
    factory = RecordingFactory()
    async with app.router.lifespan_context(app):
        pool = CredentialClientPool(factory)
        app.state.live_check_clients = pool
        await pool.client_for(_credential())
        client = factory.clients[0]
        assert client.closed is False

    # Then the shutdown closed it, so a process that has been up for weeks does
    # not hold a socket per credential it ever called
    assert client.closed is True
