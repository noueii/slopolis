"""BYOK provider credentials, model catalog, and role→model assignments (spec 10.2).

The workspace admin's configuration surface, reached under ``/api/providers``
and ``/api/catalog``. Every route is admin-only and workspace-scoped, so one
workspace can never see or touch another's rows by id (an id from elsewhere is a
404), and every mutation writes an ``AuditLog`` row whose detail carries no
secret.

The API key is write-only: the vault seals it on the way in and only ``key_last4``
is ever read back. Two outbound calls exist — test-connection and catalog import —
and both GET the provider's ``/v1/models`` with the stored key. A provider being
down is a recorded status on test-connection, but an import has nothing to import
and fails with a typed error instead.

``/api/models`` (the review composer's picker) is deliberately untouched: the
admin surface lives under ``/api/catalog`` so the existing contract and its
consumers keep working.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, cast

import httpx
from fastapi import APIRouter, status
from sqlalchemy import delete, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import (
    AdminUserDep,
    AppSettingsDep,
    DbSessionDep,
    VaultDep,
    WorkspaceIdDep,
)
from app.errors import ApiError
from app.schemas import (
    AssignmentResponse,
    AssignmentUpdateRequest,
    CatalogModelCreateRequest,
    CatalogModelListResponse,
    CatalogModelRef,
    ModelImportRequest,
    ModelImportResponse,
    ProviderCreateRequest,
    ProviderCredentialRef,
    ProviderListResponse,
    ProviderTestResult,
    ProviderUpdateRequest,
    RoleAssignmentRef,
)
from slopolis_core.roles import ASSIGNABLE_ROLES, is_assignable_role
from slopolis_core.vault import SecretVault, VaultDecryptError
from slopolis_db.models import (
    AuditLog,
    ModelAssignment,
    ModelCatalog,
    ProviderCredential,
)

__all__ = ["router"]

router = APIRouter(tags=["providers"])

#: The test-connection and import probes are interactive, so they stay short.
_PROBE_TIMEOUT_S = 10.0
_DETAIL_MAX = 300
_AUTO = "auto"


# --- serialization ----------------------------------------------------------


def _serialize_credential(row: ProviderCredential) -> ProviderCredentialRef:
    """Map a credential row onto the wire reference (never the key)."""
    return ProviderCredentialRef(
        id=str(row.id),
        provider=row.provider,
        base_url=row.base_url,
        key_last4=row.key_last4,
        enabled=row.enabled,
        last_status=row.last_status,
        last_checked_at=row.last_checked_at,
        created_at=row.created_at,
    )


def _serialize_model(row: ModelCatalog) -> CatalogModelRef:
    """Map a catalog row onto the wire reference."""
    return CatalogModelRef(
        id=str(row.id),
        model_id=row.model_id,
        provider=row.provider,
        display_name=row.display_name,
        source=row.source,
        credential_id=None if row.credential_id is None else str(row.credential_id),
    )


def _audit(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    actor_id: uuid.UUID,
    action: str,
    target_type: str,
    target_id: uuid.UUID | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    """Stage one audit row for the caller to commit with its mutation."""
    db.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_user_id=actor_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            detail=detail,
        )
    )


# --- lookups ----------------------------------------------------------------


async def _require_credential(
    db: AsyncSession, credential_id: uuid.UUID, workspace_id: uuid.UUID
) -> ProviderCredential:
    """Load a credential in this workspace, or 404."""
    row = await db.scalar(
        select(ProviderCredential).where(
            ProviderCredential.id == credential_id,
            ProviderCredential.workspace_id == workspace_id,
        )
    )
    if row is None:
        raise ApiError(
            404,
            "credential_not_found",
            "That provider credential is not in this workspace.",
        )
    return row


async def _catalog_rows(
    db: AsyncSession, workspace_id: uuid.UUID
) -> list[ModelCatalog]:
    """Return the workspace catalog, oldest first, with a stable tiebreaker.

    ``created_at`` is a transaction timestamp on Postgres and second-resolution
    on SQLite, so rows written together tie; the model id keeps the "first row is
    the default" rule deterministic.
    """
    rows = await db.scalars(
        select(ModelCatalog)
        .where(ModelCatalog.workspace_id == workspace_id)
        .order_by(ModelCatalog.created_at, ModelCatalog.model_id)
    )
    return list(rows.all())


async def _assignment_row(
    db: AsyncSession, workspace_id: uuid.UUID, role: str
) -> ModelAssignment | None:
    """Return the assignment row for ``role``, or ``None`` for ``auto``."""
    return await db.scalar(
        select(ModelAssignment).where(
            ModelAssignment.workspace_id == workspace_id,
            ModelAssignment.role == role,
        )
    )


def _parse_uuid(value: str) -> uuid.UUID:
    """Parse a wire id, answering a 404 exactly as a path-addressed id would."""
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise ApiError(
            404,
            "credential_not_found",
            "That provider credential is not in this workspace.",
        ) from exc


def _assigned_model(assignment: ModelAssignment | None, known: set[str]) -> str | None:
    """Resolve a role's stored model id, reporting ``auto`` for a stale row.

    A row can outlive its model (the catalog FK nulls the link), and reporting a
    stale id would advertise a model nothing can serve, so it reads as ``auto``.
    """
    if assignment is None or assignment.model_id not in known:
        return None
    return assignment.model_id


async def _clear_assignments(
    db: AsyncSession, workspace_id: uuid.UUID, model_ids: list[str]
) -> int:
    """Delete assignments pointing at models that no longer exist.

    Roles then fall back to ``auto`` instead of pointing at a model nothing can
    serve. Returns how many rows were cleared, for the audit detail.
    """
    if not model_ids:
        return 0
    result = await db.execute(
        delete(ModelAssignment).where(
            ModelAssignment.workspace_id == workspace_id,
            ModelAssignment.model_id.in_(model_ids),
        )
    )
    return cast("CursorResult[Any]", result).rowcount


# --- provider probes --------------------------------------------------------


def _clean_base_url(value: str | None) -> str | None:
    """Normalize an optional base URL; blank or absent means the configured default."""
    if value is None:
        return None
    return value.strip().rstrip("/") or None


def _open_secret(vault: SecretVault, blob: bytes) -> str:
    """Decrypt a stored key, or 503 when the vault key no longer opens it.

    A credential sealed under a previous ``ENCRYPTION_KEY`` is a server
    configuration error (spec 10.2), not a failed provider call: the operator
    changed the key, so the fix is to rotate the credential, not the network.
    """
    try:
        return vault.open(blob)
    except VaultDecryptError as exc:
        raise ApiError(
            503,
            "vault_not_configured",
            "The stored credential cannot be decrypted with the current ENCRYPTION_KEY.",
        ) from exc


def _model_ids(payload: object) -> list[str]:
    """Read ``data[].id`` out of a ``/v1/models`` body, tolerating anything else."""
    if not isinstance(payload, dict):
        return []
    data = cast("dict[str, object]", payload).get("data")
    if not isinstance(data, list):
        return []
    model_ids: list[str] = []
    for entry in cast("list[object]", data):
        if not isinstance(entry, dict):
            continue
        value = cast("dict[str, object]", entry).get("id")
        if isinstance(value, str) and value.strip() and value.strip() not in model_ids:
            model_ids.append(value.strip())
    return model_ids


def _provider_message(response: httpx.Response) -> str | None:
    """Extract the provider's own message from a failed response, if any."""
    try:
        payload = response.json()
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    body = cast("dict[str, object]", payload)
    for key in ("message", "error", "detail"):
        value = body.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict):
            nested = cast("dict[str, object]", value).get("message")
            if isinstance(nested, str) and nested.strip():
                return nested.strip()
    return None


def _short_detail(text: str, api_key: str) -> str:
    """Trim a provider detail and make sure the key cannot appear in it.

    A provider is free to echo the submitted key back; that text would otherwise
    land in an audit row and in the response.
    """
    redacted = text.replace(api_key, "***") if api_key else text
    collapsed = " ".join(redacted.split())
    if len(collapsed) > _DETAIL_MAX:
        return collapsed[: _DETAIL_MAX - 3] + "..."
    return collapsed


async def _probe_models(
    base_url: str, api_key: str
) -> tuple[list[str] | None, str | None]:
    """GET ``{base_url}/v1/models``; return ``(model ids, failure detail)``.

    Exactly one of the two is set: ``None`` ids mean the probe failed (transport
    error or non-2xx), and the detail explains it without ever carrying the key.
    """
    url = f"{base_url.rstrip('/')}/v1/models"
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT_S) as client:
            response = await client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        return None, _short_detail(f"{type(exc).__name__}: {exc}", api_key)
    if response.status_code >= status.HTTP_300_MULTIPLE_CHOICES:
        message = _provider_message(response)
        prefix = f"HTTP {response.status_code}"
        return None, _short_detail(
            prefix if message is None else f"{prefix}: {message}", api_key
        )
    try:
        payload: object = response.json()
    except ValueError:
        return None, "The provider model list was not JSON."
    return _model_ids(payload), None


# --- providers --------------------------------------------------------------


@router.get("/providers")
async def list_providers(
    db: DbSessionDep, workspace_id: WorkspaceIdDep, _admin: AdminUserDep
) -> ProviderListResponse:
    """Return every credential the workspace holds, oldest first."""
    rows = await db.scalars(
        select(ProviderCredential)
        .where(ProviderCredential.workspace_id == workspace_id)
        .order_by(ProviderCredential.created_at, ProviderCredential.provider)
    )
    return ProviderListResponse(
        items=[_serialize_credential(row) for row in rows.all()]
    )


@router.post("/providers", status_code=status.HTTP_201_CREATED)
async def create_provider(
    payload: ProviderCreateRequest,
    db: DbSessionDep,
    workspace_id: WorkspaceIdDep,
    admin: AdminUserDep,
    vault: VaultDep,
) -> ProviderCredentialRef:
    """Seal a new credential and return its masked reference."""
    provider = payload.provider.strip()
    if not provider:
        raise ApiError(422, "provider_required", "Provide a provider name.")
    api_key = payload.api_key.strip()
    if not api_key:
        raise ApiError(422, "api_key_required", "Provide an API key.")

    row = ProviderCredential(
        workspace_id=workspace_id,
        provider=provider,
        base_url=_clean_base_url(payload.base_url),
        encrypted_api_key=vault.seal(api_key),
        key_last4=SecretVault.last4(api_key),
        enabled=True,
    )
    db.add(row)
    await db.flush()
    _audit(
        db,
        workspace_id=workspace_id,
        actor_id=admin.id,
        action="provider.created",
        target_type="provider_credential",
        target_id=row.id,
        detail={
            "provider": row.provider,
            "baseUrl": row.base_url,
            "keyLast4": row.key_last4,
        },
    )
    await db.commit()
    await db.refresh(row)
    return _serialize_credential(row)


@router.patch("/providers/{id}")
async def update_provider(
    id: uuid.UUID,
    payload: ProviderUpdateRequest,
    db: DbSessionDep,
    workspace_id: WorkspaceIdDep,
    admin: AdminUserDep,
    vault: VaultDep,
) -> ProviderCredentialRef:
    """Update a credential in place; the key is re-sealed only when supplied."""
    row = await _require_credential(db, id, workspace_id)
    if payload.base_url is not None:
        row.base_url = _clean_base_url(payload.base_url)
    if payload.enabled is not None:
        row.enabled = payload.enabled
    if payload.api_key is not None:
        api_key = payload.api_key.strip()
        if not api_key:
            raise ApiError(422, "api_key_required", "Provide an API key.")
        row.encrypted_api_key = vault.seal(api_key)
        row.key_last4 = SecretVault.last4(api_key)

    _audit(
        db,
        workspace_id=workspace_id,
        actor_id=admin.id,
        action="provider.updated",
        target_type="provider_credential",
        target_id=row.id,
        detail={
            "provider": row.provider,
            "baseUrl": row.base_url,
            "enabled": row.enabled,
            "keyRotated": payload.api_key is not None,
        },
    )
    await db.commit()
    await db.refresh(row)
    return _serialize_credential(row)


@router.delete("/providers/{id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_provider(
    id: uuid.UUID,
    db: DbSessionDep,
    workspace_id: WorkspaceIdDep,
    admin: AdminUserDep,
) -> None:
    """Delete a credential and the catalog rows imported through it."""
    row = await _require_credential(db, id, workspace_id)
    model_ids = list(
        (
            await db.scalars(
                select(ModelCatalog.model_id).where(
                    ModelCatalog.workspace_id == workspace_id,
                    ModelCatalog.credential_id == row.id,
                )
            )
        ).all()
    )
    result = await db.execute(
        delete(ModelCatalog).where(
            ModelCatalog.workspace_id == workspace_id,
            ModelCatalog.credential_id == row.id,
        )
    )
    cleared = await _clear_assignments(db, workspace_id, model_ids)
    await db.delete(row)
    _audit(
        db,
        workspace_id=workspace_id,
        actor_id=admin.id,
        action="provider.deleted",
        target_type="provider_credential",
        target_id=row.id,
        detail={
            "provider": row.provider,
            "keyLast4": row.key_last4,
            "modelsRemoved": cast("CursorResult[Any]", result).rowcount,
            "assignmentsCleared": cleared,
        },
    )
    await db.commit()


@router.post("/providers/{id}/test")
async def test_provider(
    id: uuid.UUID,
    db: DbSessionDep,
    workspace_id: WorkspaceIdDep,
    admin: AdminUserDep,
    vault: VaultDep,
    settings: AppSettingsDep,
) -> ProviderTestResult:
    """Probe the provider's model list and record the outcome on the row."""
    row = await _require_credential(db, id, workspace_id)
    api_key = _open_secret(vault, row.encrypted_api_key)
    base_url = row.base_url or settings.core.litellm_base_url
    _ids, failure = await _probe_models(base_url, api_key)

    checked_at = dt.datetime.now(dt.UTC)
    outcome = "failed" if failure is not None else "ok"
    row.last_status = outcome
    row.last_checked_at = checked_at
    _audit(
        db,
        workspace_id=workspace_id,
        actor_id=admin.id,
        action="provider.test",
        target_type="provider_credential",
        target_id=row.id,
        detail={
            "provider": row.provider,
            "status": outcome,
            "detail": failure,
        },
    )
    await db.commit()
    return ProviderTestResult(status=outcome, detail=failure, checked_at=checked_at)


# --- model catalog ----------------------------------------------------------


@router.get("/catalog/models")
async def list_catalog_models(
    db: DbSessionDep, workspace_id: WorkspaceIdDep, _admin: AdminUserDep
) -> CatalogModelListResponse:
    """Return the workspace catalog; the first row is what ``auto`` resolves to."""
    rows = await _catalog_rows(db, workspace_id)
    return CatalogModelListResponse(
        items=[_serialize_model(row) for row in rows],
        default_model_id=rows[0].model_id if rows else None,
    )


@router.post("/catalog/models", status_code=status.HTTP_201_CREATED)
async def create_catalog_model(
    payload: CatalogModelCreateRequest,
    db: DbSessionDep,
    workspace_id: WorkspaceIdDep,
    admin: AdminUserDep,
) -> CatalogModelRef:
    """Add a manual model id to the workspace catalog."""
    model_id = payload.model_id.strip()
    if not model_id:
        raise ApiError(422, "model_id_required", "Provide a model id.")
    provider = payload.provider.strip()
    if not provider:
        raise ApiError(422, "provider_required", "Provide a provider name.")

    existing = await db.scalar(
        select(ModelCatalog).where(
            ModelCatalog.workspace_id == workspace_id,
            ModelCatalog.model_id == model_id,
        )
    )
    if existing is not None:
        raise ApiError(
            409, "model_exists", "That model is already in the workspace catalog."
        )

    row = ModelCatalog(
        workspace_id=workspace_id,
        credential_id=None,
        model_id=model_id,
        provider=provider,
        display_name=(payload.display_name or "").strip() or None,
        source="manual",
    )
    db.add(row)
    await db.flush()
    _audit(
        db,
        workspace_id=workspace_id,
        actor_id=admin.id,
        action="model.created",
        target_type="model_catalog",
        target_id=row.id,
        detail={"modelId": row.model_id, "provider": row.provider, "source": row.source},
    )
    await db.commit()
    await db.refresh(row)
    return _serialize_model(row)


@router.post("/catalog/models/import")
async def import_catalog_models(
    payload: ModelImportRequest,
    db: DbSessionDep,
    workspace_id: WorkspaceIdDep,
    admin: AdminUserDep,
    vault: VaultDep,
    settings: AppSettingsDep,
) -> ModelImportResponse:
    """Import the provider's model list, refreshing models already in the catalog."""
    credential_id = _parse_uuid(payload.credential_id)
    credential = await _require_credential(db, credential_id, workspace_id)
    api_key = _open_secret(vault, credential.encrypted_api_key)
    base_url = credential.base_url or settings.core.litellm_base_url
    model_ids, failure = await _probe_models(base_url, api_key)
    if model_ids is None:
        raise ApiError(
            502,
            "provider_unavailable",
            failure or "The provider model list could not be read.",
        )

    existing = {row.model_id: row for row in await _catalog_rows(db, workspace_id)}
    imported = 0
    rows: list[ModelCatalog] = []
    for model_id in model_ids:
        row = existing.get(model_id)
        if row is None:
            row = ModelCatalog(
                workspace_id=workspace_id,
                credential_id=credential.id,
                model_id=model_id,
                provider=credential.provider,
                display_name=None,
                source="import",
            )
            db.add(row)
            existing[model_id] = row
            imported += 1
        else:
            # Already known: refresh which credential and provider serves it, and
            # never count it as newly imported.
            row.provider = credential.provider
            row.credential_id = credential.id
        rows.append(row)

    await db.flush()
    _audit(
        db,
        workspace_id=workspace_id,
        actor_id=admin.id,
        action="model.imported",
        target_type="provider_credential",
        target_id=credential.id,
        detail={
            "provider": credential.provider,
            "imported": imported,
            "listed": len(model_ids),
        },
    )
    await db.commit()
    return ModelImportResponse(
        imported=imported, items=[_serialize_model(row) for row in rows]
    )


@router.delete("/catalog/models/{id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_catalog_model(
    id: uuid.UUID,
    db: DbSessionDep,
    workspace_id: WorkspaceIdDep,
    admin: AdminUserDep,
) -> None:
    """Delete a catalog row by its own id and clear the assignments pointing at it."""
    row = await db.scalar(
        select(ModelCatalog).where(
            ModelCatalog.id == id,
            ModelCatalog.workspace_id == workspace_id,
        )
    )
    if row is None:
        raise ApiError(
            404, "model_not_found", "That model is not in the workspace catalog."
        )
    cleared = await _clear_assignments(db, workspace_id, [row.model_id])
    await db.delete(row)
    _audit(
        db,
        workspace_id=workspace_id,
        actor_id=admin.id,
        action="model.deleted",
        target_type="model_catalog",
        target_id=row.id,
        detail={"modelId": row.model_id, "assignmentsCleared": cleared},
    )
    await db.commit()


# --- role assignments -------------------------------------------------------


@router.get("/catalog/assignments")
async def list_assignments(
    db: DbSessionDep, workspace_id: WorkspaceIdDep, _admin: AdminUserDep
) -> AssignmentResponse:
    """Return the workspace default plus one entry per assignable role."""
    catalog = await _catalog_rows(db, workspace_id)
    known = {row.model_id for row in catalog}
    rows = await db.scalars(
        select(ModelAssignment).where(ModelAssignment.workspace_id == workspace_id)
    )
    assignments = {row.role: row for row in rows.all()}
    return AssignmentResponse(
        default_model_id=catalog[0].model_id if catalog else None,
        roles=[
            RoleAssignmentRef(
                role=role, model_id=_assigned_model(assignments.get(role), known)
            )
            for role in ASSIGNABLE_ROLES
        ],
    )


@router.put("/catalog/assignments/{role}")
async def update_assignment(
    role: str,
    payload: AssignmentUpdateRequest,
    db: DbSessionDep,
    workspace_id: WorkspaceIdDep,
    admin: AdminUserDep,
) -> RoleAssignmentRef:
    """Point a role at a catalog model, or delete the row to fall back to ``auto``."""
    if not is_assignable_role(role):
        raise ApiError(
            422, "unknown_role", f"'{role}' is not an assignable model role."
        )

    requested = (payload.model_id or "").strip()
    if not requested or requested == _AUTO:
        # `auto` is the absence of a row, not a stored sentinel.
        existing = await _assignment_row(db, workspace_id, role)
        if existing is not None:
            target_id = existing.id
            await db.delete(existing)
            _audit(
                db,
                workspace_id=workspace_id,
                actor_id=admin.id,
                action="assignment.updated",
                target_type="model_assignment",
                target_id=target_id,
                detail={"role": role, "modelId": None},
            )
            await db.commit()
        return RoleAssignmentRef(role=role, model_id=None)

    model = await db.scalar(
        select(ModelCatalog).where(
            ModelCatalog.workspace_id == workspace_id,
            ModelCatalog.model_id == requested,
        )
    )
    if model is None:
        raise ApiError(
            422,
            "unknown_model",
            "Assign a model the workspace has imported or added.",
        )

    assignment = await _assignment_row(db, workspace_id, role)
    if assignment is None:
        assignment = ModelAssignment(workspace_id=workspace_id, role=role)
        db.add(assignment)
    assignment.model_catalog_id = model.id
    assignment.model_id = model.model_id
    await db.flush()
    _audit(
        db,
        workspace_id=workspace_id,
        actor_id=admin.id,
        action="assignment.updated",
        target_type="model_assignment",
        target_id=assignment.id,
        detail={"role": role, "modelId": model.model_id},
    )
    await db.commit()
    return RoleAssignmentRef(role=role, model_id=model.model_id)
