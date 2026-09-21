"""BYOK provider & model configuration (spec 10.2).

Covers the vault-backed credential lifecycle, the model catalog (import and
manual), and role assignments. Provider traffic is faked with ``respx`` at the
credential's base URL, so the only real crypto is the vault's.
"""

from __future__ import annotations

import base64
import json
import uuid
from collections.abc import Iterator
from typing import Any, cast

import httpx
import pytest
import respx
from app.deps import get_vault
from sqlalchemy import select

from slopolis_core.roles import ASSIGNABLE_ROLES
from slopolis_core.settings import get_settings
from slopolis_db.models import (
    AuditLog,
    ModelAssignment,
    ModelCatalog,
    ProviderCredential,
    User,
    Workspace,
)

from .conftest import ApiHarness, seed_workspace

_PROVIDER_URL = "https://litellm.example"
_MODELS_URL = f"{_PROVIDER_URL}/v1/models"
_API_KEY = "sk-live-abcdefgh"
_MASTER_KEY = base64.b64encode(b"provider-tests-master-key-material").decode()
_ROTATED_KEY = "sk-rotated-9999"


@pytest.fixture(autouse=True)
def vault_key(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Give every test a usable vault key and drop the settings/vault memos."""
    monkeypatch.setenv("ENCRYPTION_KEY", _MASTER_KEY)
    reset_vault()
    yield
    reset_vault()


def reset_vault() -> None:
    """Clear both cached settings and the cached vault after an env change."""
    get_settings.cache_clear()
    get_vault.cache_clear()


def sent_requests(route: respx.Route) -> list[httpx.Request]:
    """Return a route's recorded requests, typed for the assertions below."""
    calls = cast("list[Any]", list(route.calls))
    return [cast("httpx.Request", call.request) for call in calls]


async def seed_admin(session_factory: Any) -> uuid.UUID:
    """Seed one workspace whose member is its admin; return the user id."""
    async with session_factory() as session:
        _workspace, user, _repository = await seed_workspace(session)
        user.is_admin = True
        await session.commit()
        return user.id


async def seed_other_admin(session_factory: Any) -> uuid.UUID:
    """Seed a second workspace with its own admin, for scoping assertions."""
    async with session_factory() as session:
        workspace = Workspace(name="Globex", slug="globex")
        session.add(workspace)
        await session.flush()
        user = User(
            workspace_id=workspace.id,
            github_id=3131,
            handle="globex-admin",
            name="Globex Admin",
            avatar_url=None,
            is_admin=True,
        )
        session.add(user)
        await session.commit()
        return user.id


async def create_provider(harness: ApiHarness) -> dict[str, Any]:
    """POST one litellm credential and return its wire reference."""
    response = await harness.client.post(
        "/api/providers",
        json={"provider": "litellm", "baseUrl": _PROVIDER_URL, "apiKey": _API_KEY},
    )
    assert response.status_code == 201
    body: dict[str, Any] = response.json()
    return body


async def create_model(harness: ApiHarness, model_id: str = "my-model") -> dict[str, Any]:
    """POST one manual catalog row and return its wire reference."""
    response = await harness.client.post(
        "/api/catalog/models",
        json={"modelId": model_id, "provider": "Custom", "displayName": "My Model"},
    )
    assert response.status_code == 201
    body: dict[str, Any] = response.json()
    return body


async def test_creating_a_provider_masks_the_key_everywhere(
    session_factory: Any, build_harness: Any
) -> None:
    # Given an admin in a workspace with a key to store
    harness: ApiHarness = await build_harness(user_id=await seed_admin(session_factory))

    # When the credential is created and listed
    created = await create_provider(harness)
    listed = await harness.client.get("/api/providers")

    # Then only the last four characters come back, on both responses
    assert created["keyLast4"] == "efgh"
    assert created["enabled"] is True
    assert created["baseUrl"] == _PROVIDER_URL
    assert created["lastStatus"] is None
    assert created["lastCheckedAt"] is None
    assert "apiKey" not in created
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()["items"]] == [created["id"]]
    assert listed.json()["items"][0]["keyLast4"] == "efgh"
    assert _API_KEY not in json.dumps(created) + listed.text

    async with session_factory() as session:
        row = await session.scalar(select(ProviderCredential))
        assert row is not None
        # The column holds a versioned ciphertext blob, never the key
        assert row.encrypted_api_key[0] == 0x01
        assert _API_KEY.encode() not in row.encrypted_api_key
        # And the audit row explains the change without the secret
        audits = list((await session.scalars(select(AuditLog))).all())
        assert [audit.action for audit in audits] == ["provider.created"]
        assert audits[0].target_id == row.id
        assert _API_KEY not in json.dumps(audits[0].detail)


@respx.mock
async def test_patching_updates_in_place_and_only_reseals_a_new_key(
    session_factory: Any, build_harness: Any
) -> None:
    # Given a stored credential
    harness: ApiHarness = await build_harness(user_id=await seed_admin(session_factory))
    provider = await create_provider(harness)
    respx.get(_MODELS_URL).mock(return_value=httpx.Response(200, json={"data": []}))

    # When only the switch and base URL change
    patched = await harness.client.patch(
        f"/api/providers/{provider['id']}",
        json={"enabled": False, "baseUrl": f"{_PROVIDER_URL}/"},
    )

    # Then the row is updated in place and the key is untouched
    assert patched.status_code == 200
    assert patched.json()["enabled"] is False
    assert patched.json()["baseUrl"] == _PROVIDER_URL
    assert patched.json()["keyLast4"] == "efgh"

    # When the key is rotated, the stored blob is replaced and the probe uses it
    rotated = await harness.client.patch(
        f"/api/providers/{provider['id']}", json={"apiKey": _ROTATED_KEY}
    )
    await harness.client.patch(
        f"/api/providers/{provider['id']}", json={"enabled": True}
    )
    route = respx.get(_MODELS_URL).mock(
        return_value=httpx.Response(200, json={"data": [{"id": "gpt-4o"}]})
    )
    tested = await harness.client.post(f"/api/providers/{provider['id']}/test")

    assert rotated.json()["keyLast4"] == "9999"
    assert rotated.json()["enabled"] is False
    assert tested.json()["status"] == "ok"
    assert sent_requests(route)[0].headers["authorization"] == f"Bearer {_ROTATED_KEY}"


async def test_non_admins_are_refused(
    seeded: Any, build_harness: Any
) -> None:
    # Given a signed-in workspace member who is not an admin
    harness: ApiHarness = await build_harness(user_id=seeded.user_id)
    admin_routes: list[tuple[str, str, dict[str, Any]]] = [
        ("get", "/api/providers", {}),
        ("post", "/api/providers", {"json": {"provider": "litellm", "apiKey": _API_KEY}}),
        ("get", "/api/catalog/models", {}),
        ("get", "/api/catalog/assignments", {}),
        ("put", "/api/catalog/assignments/review", {"json": {"modelId": None}}),
        ("post", "/api/catalog/models", {"json": {"modelId": "m", "provider": "p"}}),
    ]

    # When they touch any administration route
    for method, path, kwargs in admin_routes:
        response = await getattr(harness.client, method)(path, **kwargs)

        # Then it is refused with the admin code
        assert response.status_code == 403, path
        assert response.json()["error"]["code"] == "admin_required", path


async def test_an_unconfigured_vault_is_a_503(
    session_factory: Any, build_harness: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given an admin but no encryption key
    user_id = await seed_admin(session_factory)
    monkeypatch.setenv("ENCRYPTION_KEY", "")
    reset_vault()
    harness: ApiHarness = await build_harness(user_id=user_id)

    # When a credential is stored
    response = await harness.client.post(
        "/api/providers", json={"provider": "litellm", "apiKey": _API_KEY}
    )

    # Then it is a typed configuration error, and reads still work
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "vault_not_configured"
    assert (await harness.client.get("/api/providers")).status_code == 200


async def test_a_credential_sealed_under_another_key_is_a_503(
    session_factory: Any, build_harness: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given a credential sealed under the original key
    harness: ApiHarness = await build_harness(user_id=await seed_admin(session_factory))
    provider = await create_provider(harness)
    monkeypatch.setenv(
        "ENCRYPTION_KEY", base64.b64encode(b"a-different-master-key-material").decode()
    )
    reset_vault()

    # When the provider is probed
    response = await harness.client.post(f"/api/providers/{provider['id']}/test")

    # Then the undecryptable credential is a configuration error, not a 401
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "vault_not_configured"


@respx.mock
async def test_test_connection_records_ok_with_the_stored_key(
    session_factory: Any, build_harness: Any
) -> None:
    # Given a stored credential and a provider that answers
    harness: ApiHarness = await build_harness(user_id=await seed_admin(session_factory))
    provider = await create_provider(harness)
    route = respx.get(_MODELS_URL).mock(
        return_value=httpx.Response(200, json={"data": [{"id": "gpt-4o"}]})
    )

    # When the connection is tested
    response = await harness.client.post(f"/api/providers/{provider['id']}/test")

    # Then the decrypted key is sent and the outcome is recorded on the row
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["detail"] is None
    assert response.json()["checkedAt"]
    assert sent_requests(route)[0].headers["authorization"] == f"Bearer {_API_KEY}"
    async with session_factory() as session:
        row = await session.scalar(select(ProviderCredential))
        assert row is not None
        assert row.last_status == "ok"
        assert row.last_checked_at is not None


@respx.mock
async def test_test_connection_records_a_failure_without_the_key(
    session_factory: Any, build_harness: Any
) -> None:
    # Given a provider that rejects the key and echoes it back in its message
    harness: ApiHarness = await build_harness(user_id=await seed_admin(session_factory))
    provider = await create_provider(harness)
    respx.get(_MODELS_URL).mock(
        return_value=httpx.Response(401, json={"error": f"invalid key {_API_KEY}"})
    )

    # When the connection is tested
    response = await harness.client.post(f"/api/providers/{provider['id']}/test")

    # Then the failure is a 200 status, reported without the key
    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert "401" in response.json()["detail"]
    assert _API_KEY not in response.text
    assert _API_KEY not in json.dumps(response.json())
    async with session_factory() as session:
        row = await session.scalar(select(ProviderCredential))
        assert row is not None
        assert row.last_status == "failed"


@respx.mock
async def test_a_credential_without_a_base_url_uses_the_configured_gateway(
    session_factory: Any, build_harness: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given the workspace gateway is configured and the credential names no URL
    monkeypatch.setenv("LITELLM_BASE_URL", "https://gateway.example")
    reset_vault()
    harness: ApiHarness = await build_harness(user_id=await seed_admin(session_factory))
    created = await harness.client.post(
        "/api/providers", json={"provider": "litellm", "apiKey": _API_KEY}
    )
    assert created.json()["baseUrl"] is None
    route = respx.get("https://gateway.example/v1/models").mock(
        return_value=httpx.Response(200, json={"data": [{"id": "gpt-4o"}]})
    )

    # When the connection is tested
    response = await harness.client.post(f"/api/providers/{created.json()['id']}/test")

    # Then the probe goes to the configured gateway
    assert response.json()["status"] == "ok"
    assert len(sent_requests(route)) == 1


@respx.mock
async def test_test_connection_records_a_transport_failure(
    session_factory: Any, build_harness: Any
) -> None:
    # Given a provider that cannot be reached
    harness: ApiHarness = await build_harness(user_id=await seed_admin(session_factory))
    provider = await create_provider(harness)
    respx.get(_MODELS_URL).mock(side_effect=httpx.ConnectError("no route to host"))

    # When the connection is tested
    response = await harness.client.post(f"/api/providers/{provider['id']}/test")

    # Then the transport failure is a recorded status too
    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert "ConnectError" in response.json()["detail"]


@respx.mock
async def test_import_creates_rows_then_refreshes_them(
    session_factory: Any, build_harness: Any
) -> None:
    # Given a provider listing models, including junk entries to ignore
    harness: ApiHarness = await build_harness(user_id=await seed_admin(session_factory))
    provider = await create_provider(harness)
    respx.get(_MODELS_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "object": "list",
                "data": [
                    {"id": "gpt-4o"},
                    {"id": "claude-sonnet-4"},
                    {"id": "gpt-4o"},
                    {"bogus": True},
                    "not-an-object",
                ],
            },
        )
    )

    # When the catalog is imported twice
    first = await harness.client.post(
        "/api/catalog/models/import", json={"credentialId": provider["id"]}
    )
    second = await harness.client.post(
        "/api/catalog/models/import", json={"credentialId": provider["id"]}
    )

    # Then only the first call counts new rows
    assert first.status_code == 200
    assert first.json()["imported"] == 2
    assert [item["modelId"] for item in first.json()["items"]] == [
        "gpt-4o",
        "claude-sonnet-4",
    ]
    assert all(
        item["source"] == "import" and item["credentialId"] == provider["id"]
        for item in first.json()["items"]
    )
    assert second.status_code == 200
    assert second.json()["imported"] == 0
    assert len(second.json()["items"]) == 2

    # And the catalog lists them with the first row as the workspace default
    listed = (await harness.client.get("/api/catalog/models")).json()
    assert len(listed["items"]) == 2
    assert listed["defaultModelId"] == listed["items"][0]["modelId"]


@respx.mock
async def test_import_reports_an_unreachable_provider(
    session_factory: Any, build_harness: Any
) -> None:
    # Given a provider that is down
    harness: ApiHarness = await build_harness(user_id=await seed_admin(session_factory))
    provider = await create_provider(harness)
    respx.get(_MODELS_URL).mock(return_value=httpx.Response(503, text="upstream down"))

    # When the catalog is imported
    response = await harness.client.post(
        "/api/catalog/models/import", json={"credentialId": provider["id"]}
    )

    # Then there is nothing to import and the caller is told so
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "provider_unavailable"
    assert "503" in response.json()["error"]["message"]


async def test_manual_models_are_unique_per_workspace(
    session_factory: Any, build_harness: Any
) -> None:
    # Given a workspace with one manual model
    harness: ApiHarness = await build_harness(user_id=await seed_admin(session_factory))
    created = await create_model(harness)

    # When the same model id is added again
    duplicate = await harness.client.post(
        "/api/catalog/models", json={"modelId": "my-model", "provider": "Custom"}
    )

    # Then the first row is manual and the duplicate is a conflict
    assert created["source"] == "manual"
    assert created["credentialId"] is None
    assert created["displayName"] == "My Model"
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "model_exists"
    assert len((await harness.client.get("/api/catalog/models")).json()["items"]) == 1


async def test_assignments_round_trip_and_validate(
    session_factory: Any, build_harness: Any
) -> None:
    # Given a workspace with one catalog model
    harness: ApiHarness = await build_harness(user_id=await seed_admin(session_factory))
    await create_model(harness)

    # When a role is pointed at it
    assigned = await harness.client.put(
        "/api/catalog/assignments/review", json={"modelId": "my-model"}
    )

    # Then every assignable role is reported, with the rest on auto
    assert assigned.status_code == 200
    assert assigned.json() == {"role": "review", "modelId": "my-model"}
    body = (await harness.client.get("/api/catalog/assignments")).json()
    assert body["defaultModelId"] == "my-model"
    assert [entry["role"] for entry in body["roles"]] == list(ASSIGNABLE_ROLES)
    assert body["roles"][0] == {"role": "review", "modelId": "my-model"}
    assert all(entry["modelId"] is None for entry in body["roles"][1:])
    async with session_factory() as session:
        row = await session.scalar(select(ModelAssignment))
        model = await session.scalar(select(ModelCatalog))
        assert row is not None and model is not None
        assert row.model_id == "my-model"
        assert row.model_catalog_id == model.id

    # When the assignment is cleared with the `auto` sentinel
    cleared = await harness.client.put(
        "/api/catalog/assignments/review", json={"modelId": "auto"}
    )

    # Then the row is gone, not stored as a sentinel
    assert cleared.status_code == 200
    assert cleared.json() == {"role": "review", "modelId": None}
    assert (
        await harness.client.get("/api/catalog/assignments")
    ).json()["roles"][0]["modelId"] is None
    async with session_factory() as session:
        assert (await session.scalars(select(ModelAssignment))).all() == []


async def test_assignments_reject_an_unknown_role_or_model(
    session_factory: Any, build_harness: Any
) -> None:
    # Given a workspace with a catalog
    harness: ApiHarness = await build_harness(user_id=await seed_admin(session_factory))
    await create_model(harness)

    # When an unknown role or an unlisted model is assigned
    role = await harness.client.put(
        "/api/catalog/assignments/wizard", json={"modelId": None}
    )
    model = await harness.client.put(
        "/api/catalog/assignments/review", json={"modelId": "ghost"}
    )

    # Then both are validation errors and nothing is stored
    assert role.status_code == 422
    assert role.json()["error"]["code"] == "unknown_role"
    assert model.status_code == 422
    assert model.json()["error"]["code"] == "unknown_model"
    async with session_factory() as session:
        assert (await session.scalars(select(ModelAssignment))).all() == []


@respx.mock
async def test_deleting_a_credential_removes_imported_models_only(
    session_factory: Any, build_harness: Any
) -> None:
    # Given a workspace with one imported model (assigned), one manual model, and
    # a credential that a second credential would not disturb
    harness: ApiHarness = await build_harness(user_id=await seed_admin(session_factory))
    provider = await create_provider(harness)
    respx.get(_MODELS_URL).mock(
        return_value=httpx.Response(200, json={"data": [{"id": "gpt-4o"}]})
    )
    await harness.client.post(
        "/api/catalog/models/import", json={"credentialId": provider["id"]}
    )
    await create_model(harness)
    await harness.client.put("/api/catalog/assignments/review", json={"modelId": "gpt-4o"})
    await harness.client.put(
        "/api/catalog/assignments/review.fast", json={"modelId": "my-model"}
    )

    # When the credential is deleted
    response = await harness.client.delete(f"/api/providers/{provider['id']}")

    # Then the imported model is gone, the manual one survives, and the roles that
    # pointed at the imported model fall back to auto
    assert response.status_code == 204
    assert (await harness.client.get("/api/providers")).json()["items"] == []
    listed = (await harness.client.get("/api/catalog/models")).json()
    assert [item["modelId"] for item in listed["items"]] == ["my-model"]
    roles = {
        entry["role"]: entry["modelId"]
        for entry in (await harness.client.get("/api/catalog/assignments")).json()["roles"]
    }
    assert roles["review"] is None
    assert roles["review.fast"] == "my-model"


async def test_deleting_a_model_clears_the_roles_pointing_at_it(
    session_factory: Any, build_harness: Any
) -> None:
    # Given an assigned manual model
    harness: ApiHarness = await build_harness(user_id=await seed_admin(session_factory))
    model = await create_model(harness)
    await harness.client.put("/api/catalog/assignments/review", json={"modelId": "my-model"})

    # When its catalog row is deleted by row id
    response = await harness.client.delete(f"/api/catalog/models/{model['id']}")

    # Then the role falls back to auto and the row is gone for good
    assert response.status_code == 204
    listed = (await harness.client.get("/api/catalog/models")).json()
    assert listed["items"] == []
    assert listed["defaultModelId"] is None
    assignments = (await harness.client.get("/api/catalog/assignments")).json()
    assert assignments["defaultModelId"] is None
    assert all(entry["modelId"] is None for entry in assignments["roles"])
    again = await harness.client.delete(f"/api/catalog/models/{model['id']}")
    assert again.status_code == 404
    assert again.json()["error"]["code"] == "model_not_found"


async def test_configuration_is_workspace_scoped(
    session_factory: Any, build_harness: Any
) -> None:
    # Given a workspace with a credential, a model, and an assignment
    owner: ApiHarness = await build_harness(user_id=await seed_admin(session_factory))
    provider = await create_provider(owner)
    model = await create_model(owner)
    await owner.client.put("/api/catalog/assignments/review", json={"modelId": "my-model"})

    # When a second workspace's admin looks at its own configuration
    other: ApiHarness = await build_harness(user_id=await seed_other_admin(session_factory))

    # Then it sees none of the first workspace's rows
    assert (await other.client.get("/api/providers")).json()["items"] == []
    models = (await other.client.get("/api/catalog/models")).json()
    assert models["items"] == []
    assert models["defaultModelId"] is None
    assignments = (await other.client.get("/api/catalog/assignments")).json()
    assert assignments["defaultModelId"] is None
    assert all(entry["modelId"] is None for entry in assignments["roles"])

    # And the first workspace's ids are not addressable from the second
    assert (
        await other.client.delete(f"/api/providers/{provider['id']}")
    ).status_code == 404
    assert (
        await other.client.patch(
            f"/api/providers/{provider['id']}", json={"enabled": False}
        )
    ).status_code == 404
    assert (
        await other.client.delete(f"/api/catalog/models/{model['id']}")
    ).status_code == 404
    unassignable = await other.client.put(
        "/api/catalog/assignments/review", json={"modelId": "my-model"}
    )
    assert unassignable.status_code == 422
    assert unassignable.json()["error"]["code"] == "unknown_model"
