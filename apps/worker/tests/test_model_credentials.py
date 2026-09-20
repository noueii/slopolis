"""Which credential a review runs on (spec 10.2 §Which credential a model call uses).

A model call runs on the credential linked to the model it is about; the
process-level gateway is the fallback for a model with no usable credential; and
a model that has neither is a permanent configuration failure naming both ways
out. Every test runs against in-memory SQLite with fake clients; no network.
"""

from __future__ import annotations

import base64
import uuid
from collections.abc import Iterator
from typing import cast

import pytest
from arq.connections import ArqRedis
from sqlalchemy import select
from worker.credentials import (
    ResolvedCredential,
    credential_for_model,
    get_credential_vault,
)
from worker.deps import ReviewContext, SessionFactory
from worker.jobs.review_target import review_target
from worker.main import StartupCtx, on_shutdown, on_startup
from worker_fakes import (
    CATALOG_MODEL,
    CREDENTIAL_BASE_URL,
    CREDENTIAL_KEY,
    FakeCredentialClients,
    FakeLlm,
    FakeRedis,
)
from worker_seed import Harness, link_credential, seed_and_build

from slopolis_core.settings import get_settings
from slopolis_core.vault import SecretVault
from slopolis_db.models import ReviewSession, SessionTarget, SessionTargetRun

_FINDINGS_JSON = (
    '{"findings": ['
    '{"path": "src/a.py", "line": 3, "severity": "error", "category": "correctness", '
    '"message": "boom", "suggestion": "fix it", "confidence": 0.9}'
    "]}"
)

#: 32+ bytes of material, the way a deployment supplies ``ENCRYPTION_KEY``.
_MASTER_KEY = base64.b64encode(b"worker-credential-test-master-key").decode()

#: A blob sealed under some other master key: it passes the layout check and then
#: fails to decrypt, which is what a rotated ``ENCRYPTION_KEY`` looks like.
_FOREIGN_BLOB = b"\x01" + bytes(128)


def _reset_memos() -> None:
    """Refresh the cached settings and vault after an environment change."""
    get_settings.cache_clear()
    get_credential_vault.cache_clear()


@pytest.fixture
def vault_ready(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Give the process an ``ENCRYPTION_KEY`` so stored credentials can be opened."""
    monkeypatch.setenv("ENCRYPTION_KEY", _MASTER_KEY)
    _reset_memos()
    yield
    monkeypatch.delenv("ENCRYPTION_KEY", raising=False)
    _reset_memos()


@pytest.fixture
def no_vault(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """A deployment with no usable ``ENCRYPTION_KEY``: the degraded mode."""
    monkeypatch.setenv("ENCRYPTION_KEY", "")
    _reset_memos()
    yield
    monkeypatch.delenv("ENCRYPTION_KEY", raising=False)
    _reset_memos()


def _sealed(api_key: str) -> bytes:
    """Seal ``api_key`` with the vault the process currently resolves."""
    return SecretVault.from_settings().seal(api_key)


async def _run(h: Harness) -> None:
    await review_target(h.ctx, str(h.seed.session_id), str(h.seed.target_id))


async def _target(h: Harness) -> SessionTarget:
    async with h.session_factory() as db:
        loaded = await db.get(SessionTarget, h.seed.target_id)
        assert loaded is not None
        return loaded


async def _session(h: Harness) -> ReviewSession:
    async with h.session_factory() as db:
        loaded = await db.get(ReviewSession, h.seed.session_id)
        assert loaded is not None
        return loaded


async def _runs(h: Harness) -> list[SessionTargetRun]:
    async with h.session_factory() as db:
        rows = await db.execute(
            select(SessionTargetRun).where(SessionTargetRun.target_id == h.seed.target_id)
        )
        return list(rows.scalars().all())


async def test_a_credential_linked_model_runs_on_that_credential(
    session_factory: SessionFactory, vault_ready: None
) -> None:
    # Given a gateway-configured worker and a review model served by a credential
    credentials = FakeCredentialClients(_FINDINGS_JSON)
    h = await seed_and_build(
        session_factory, llm=FakeLlm([_FINDINGS_JSON]), credentials=credentials
    )
    await link_credential(
        session_factory,
        workspace_id=h.seed.workspace_id,
        encrypted_api_key=_sealed(CREDENTIAL_KEY),
    )

    # When the target runs
    await _run(h)

    # Then the credential's base URL and key carried the call ...
    assert credentials.built == [(CREDENTIAL_BASE_URL, CREDENTIAL_KEY)]
    assert credentials.llms[0].models == [CATALOG_MODEL]
    # ... and the gateway client was never asked to model anything
    assert h.seed.llm.models == []
    assert (await _session(h)).status == "done"


async def test_a_credential_serves_a_worker_with_no_gateway(
    session_factory: SessionFactory, vault_ready: None
) -> None:
    # Given a worker with no LITELLM_MASTER_KEY, whose model has a credential
    credentials = FakeCredentialClients(_FINDINGS_JSON)
    h = await seed_and_build(
        session_factory,
        llm=FakeLlm([_FINDINGS_JSON]),
        credentials=credentials,
        gateway=False,
    )
    await link_credential(
        session_factory,
        workspace_id=h.seed.workspace_id,
        encrypted_api_key=_sealed(CREDENTIAL_KEY),
    )

    # When the target runs
    await _run(h)

    # Then the review runs on the credential, with no gateway in the process
    assert credentials.built == [(CREDENTIAL_BASE_URL, CREDENTIAL_KEY)]
    assert credentials.llms[0].models == [CATALOG_MODEL]
    assert (await _target(h)).status == "done"


async def test_a_model_without_a_credential_uses_the_gateway(
    session_factory: SessionFactory,
) -> None:
    # Given a model whose catalog row links no credential
    credentials = FakeCredentialClients(_FINDINGS_JSON)
    h = await seed_and_build(
        session_factory, llm=FakeLlm([_FINDINGS_JSON]), credentials=credentials
    )

    # When the target runs
    await _run(h)

    # Then the process gateway served the call, exactly as before this feature
    assert credentials.built == []
    assert h.seed.llm.models == [CATALOG_MODEL]
    assert (await _session(h)).status == "done"


async def test_a_model_with_neither_credential_nor_gateway_fails_permanently(
    session_factory: SessionFactory,
) -> None:
    # Given a gateway-less worker and no credential linked to the review model
    h = await seed_and_build(session_factory, llm=FakeLlm([_FINDINGS_JSON]), gateway=False)

    # When the target runs
    await _run(h)

    # Then it fails once, naming the model and both ways out
    assert (await _target(h)).status == "failed"
    runs = await _runs(h)
    assert len(runs) == 1
    assert runs[0].error is not None
    assert "ModelNotConfiguredError" in runs[0].error
    assert CATALOG_MODEL in runs[0].error
    assert f"link a credential to {CATALOG_MODEL}" in runs[0].error
    assert "LITELLM_BASE_URL" in runs[0].error
    assert "LITELLM_MASTER_KEY" in runs[0].error
    assert (await _session(h)).status == "failed"


async def test_a_disabled_credential_is_ignored(
    session_factory: SessionFactory, vault_ready: None
) -> None:
    # Given a credential an admin has switched off, linked to the review model
    credentials = FakeCredentialClients(_FINDINGS_JSON)
    h = await seed_and_build(
        session_factory, llm=FakeLlm([_FINDINGS_JSON]), credentials=credentials
    )
    await link_credential(
        session_factory,
        workspace_id=h.seed.workspace_id,
        encrypted_api_key=_sealed(CREDENTIAL_KEY),
        enabled=False,
    )

    # When the target runs
    await _run(h)

    # Then it is not used, and the gateway serves the call instead
    assert credentials.built == []
    assert h.seed.llm.models == [CATALOG_MODEL]
    assert (await _session(h)).status == "done"


async def test_without_a_vault_a_linked_credential_falls_back_to_the_gateway(
    session_factory: SessionFactory, no_vault: None
) -> None:
    # Given a linked credential no process key can open, and no ENCRYPTION_KEY
    credentials = FakeCredentialClients(_FINDINGS_JSON)
    h = await seed_and_build(
        session_factory, llm=FakeLlm([_FINDINGS_JSON]), credentials=credentials
    )
    await link_credential(
        session_factory,
        workspace_id=h.seed.workspace_id,
        encrypted_api_key=_FOREIGN_BLOB,
    )

    # When the target runs
    await _run(h)

    # Then the credential is unusable rather than fatal, and the gateway runs it
    assert credentials.built == []
    assert h.seed.llm.models == [CATALOG_MODEL]
    assert (await _session(h)).status == "done"


async def test_an_unopenable_credential_falls_back_to_the_gateway(
    session_factory: SessionFactory, vault_ready: None
) -> None:
    # Given a linked credential sealed under a different master key
    credentials = FakeCredentialClients(_FINDINGS_JSON)
    h = await seed_and_build(
        session_factory, llm=FakeLlm([_FINDINGS_JSON]), credentials=credentials
    )
    await link_credential(
        session_factory,
        workspace_id=h.seed.workspace_id,
        encrypted_api_key=_FOREIGN_BLOB,
    )

    # When the target runs
    await _run(h)

    # Then the blob that will not open means "no usable credential", not a crash
    assert credentials.built == []
    assert h.seed.llm.models == [CATALOG_MODEL]
    assert (await _session(h)).status == "done"


async def test_without_a_vault_or_a_gateway_the_target_fails_naming_both(
    session_factory: SessionFactory, no_vault: None
) -> None:
    # Given a credential nothing can open and no gateway to fall back to
    h = await seed_and_build(session_factory, llm=FakeLlm([_FINDINGS_JSON]), gateway=False)
    await link_credential(
        session_factory,
        workspace_id=h.seed.workspace_id,
        encrypted_api_key=_FOREIGN_BLOB,
    )

    # When the target runs
    await _run(h)

    # Then the target fails permanently with the naming message
    assert (await _target(h)).status == "failed"
    runs = await _runs(h)
    assert len(runs) == 1
    assert runs[0].error is not None
    assert f"model {CATALOG_MODEL} has no usable credential" in runs[0].error


async def test_a_credential_without_a_url_rides_the_configured_gateway_host(
    session_factory: SessionFactory, vault_ready: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given a credential that names no base URL, and a configured gateway host
    h = await seed_and_build(session_factory, llm=FakeLlm([_FINDINGS_JSON]))
    await link_credential(
        session_factory,
        workspace_id=h.seed.workspace_id,
        encrypted_api_key=_sealed(CREDENTIAL_KEY),
        base_url=None,
    )
    monkeypatch.setenv("LITELLM_BASE_URL", "https://gateway.example")
    _reset_memos()

    # When the model's credential is resolved
    async with h.session_factory() as db:
        credential = await credential_for_model(
            db, workspace_id=h.seed.workspace_id, model_id=CATALOG_MODEL
        )

    # Then it carries the gateway host, the way the provider probe resolves it
    assert credential is not None
    assert credential.base_url == "https://gateway.example"
    assert credential.api_key == CREDENTIAL_KEY


async def test_a_worker_reuses_one_client_per_credential() -> None:
    # Given a worker that built a client for one credential
    credentials = FakeCredentialClients(_FINDINGS_JSON)
    gateway = FakeLlm([_FINDINGS_JSON])
    review = ReviewContext(llm=gateway, credential_client_factory=credentials)
    credential = ResolvedCredential(
        base_url=CREDENTIAL_BASE_URL, api_key=CREDENTIAL_KEY, credential_id=uuid.uuid4()
    )

    # When two model calls resolve their client
    first = review.client_for(model_id=CATALOG_MODEL, credential=credential)
    second = review.client_for(model_id=CATALOG_MODEL, credential=credential)

    # Then both calls share one client, so one HTTP pool spans the worker's jobs
    assert first is second
    assert len(credentials.built) == 1

    # ... and shutting the worker down closes it and the gateway, and forgets it
    await review.aclose()
    assert credentials.llms[0].closed
    assert gateway.closed
    assert review.client_for(model_id=CATALOG_MODEL, credential=credential) is not first


async def test_a_gateway_less_worker_starts_and_stops_cleanly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a deployment with no LITELLM_MASTER_KEY
    monkeypatch.setenv("LITELLM_MASTER_KEY", "")
    get_settings.cache_clear()

    # When the worker starts
    ctx: StartupCtx = {"redis": cast("ArqRedis", FakeRedis())}
    await on_startup(ctx)

    # Then it boots with no gateway client rather than refusing to start ...
    review = ctx.get("review")
    assert review is not None
    assert review.llm is None
    # ... and shuts down without one
    await on_shutdown(ctx)
