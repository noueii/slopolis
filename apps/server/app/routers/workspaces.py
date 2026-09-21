"""Workspace onboarding: create a workspace, or list the ones you belong to.

v1 is self-hosted and single-tenant, but signing in no longer hands every new
account a workspace. A user with none lands on the onboarding gate and either
creates one (becoming its admin) or waits to be invited — the invitation
mechanism itself is deferred with multi-tenancy (spec §5, Phase 5).

The router also carries the workspace's **settings** (spec 10.10): the caps the
submit path applies before it spawns any work. They hang off this prefix because
the caller's workspace is implied by their session, so the surface has one
workspace prefix rather than two. Reading and writing them is admin-only and
every change is audited.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from fastapi import APIRouter, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import AdminUserDep, CurrentUserDep, DbSessionDep, WorkspaceIdDep
from app.errors import ApiError
from app.schemas import (
    CreateWorkspaceRequest,
    WorkspaceListResponse,
    WorkspaceRef,
    WorkspaceSettings,
    WorkspaceSettingsUpdate,
)
from app.serializers import workspace_ref
from slopolis_db.models import AuditLog, User, Workspace

__all__ = ["router", "slugify"]

router = APIRouter(tags=["workspaces"])

_NAME_MAX = 255
_SLUG_MAX = 48
_SLUG_SUFFIX_LIMIT = 100
_SLUG_SEPARATORS = re.compile(r"[^a-z0-9]+")


def slugify(name: str) -> str:
    """Derive a stable, url-safe slug from a workspace name."""
    slug = _SLUG_SEPARATORS.sub("-", name.lower()).strip("-")[:_SLUG_MAX].strip("-")
    return slug or "workspace"


async def _unique_slug(db: AsyncSession, base: str) -> str:
    """Return ``base`` or the first free ``base-N`` variant."""
    taken = set(
        (await db.scalars(select(Workspace.slug).where(Workspace.slug.startswith(base)))).all()
    )
    if base not in taken:
        return base
    for suffix in range(2, _SLUG_SUFFIX_LIMIT):
        candidate = f"{base}-{suffix}"
        if candidate not in taken:
            return candidate
    raise ApiError(
        409,
        "slug_unavailable",
        "Could not derive a unique workspace slug; pick a different name.",
    )


@router.get("/workspaces")
async def list_workspaces(db: DbSessionDep, user: CurrentUserDep) -> WorkspaceListResponse:
    """Return the workspaces the caller belongs to (v1: at most one)."""
    if user.workspace_id is None:
        return WorkspaceListResponse()
    workspace = await db.get(Workspace, user.workspace_id)
    reference = workspace_ref(workspace)
    return WorkspaceListResponse(items=[] if reference is None else [reference])


@router.post("/workspaces", status_code=status.HTTP_201_CREATED)
async def create_workspace(
    payload: CreateWorkspaceRequest, db: DbSessionDep, user: CurrentUserDep
) -> WorkspaceRef:
    """Create a workspace and make the caller its admin."""
    name = payload.name.strip()
    if not name:
        raise ApiError(422, "name_required", "Give the workspace a name.")
    if len(name) > _NAME_MAX:
        raise ApiError(
            422,
            "name_too_long",
            f"Workspace names are limited to {_NAME_MAX} characters.",
        )
    # Read the caller's own row: membership is decided by what is stored, not by
    # whatever object the auth dependency handed us.
    member = await db.get(User, user.id)
    if member is None:
        raise ApiError(401, "unauthorized", "Your session is no longer valid.")
    if member.workspace_id is not None:
        raise ApiError(
            409,
            "already_in_workspace",
            "You already belong to a workspace.",
        )

    workspace = Workspace(name=name, slug=await _unique_slug(db, slugify(name)))
    db.add(workspace)
    await db.flush()

    member.workspace_id = workspace.id
    member.is_admin = True
    await db.flush()

    reference = workspace_ref(workspace)
    assert reference is not None  # just created, so never None
    return reference


# --- settings (spec 10.10) --------------------------------------------------


@router.get("/workspaces/settings")
async def get_workspace_settings(
    db: DbSessionDep, workspace_id: WorkspaceIdDep, _admin: AdminUserDep
) -> WorkspaceSettings:
    """Return the workspace's caps; an unset cap is ``null``, i.e. unlimited."""
    return WorkspaceSettings.model_validate(await _require_workspace(db, workspace_id))


@router.patch("/workspaces/settings")
async def update_workspace_settings(
    payload: WorkspaceSettingsUpdate,
    db: DbSessionDep,
    workspace_id: WorkspaceIdDep,
    admin: AdminUserDep,
) -> WorkspaceSettings:
    """Apply the supplied caps, auditing only the keys that actually changed."""
    workspace = await _require_workspace(db, workspace_id)
    changes = _apply_caps(workspace, payload)
    if not changes:
        # A PATCH that changes nothing is a read: no write and no audit row, so
        # re-sending a form is safe.
        return WorkspaceSettings.model_validate(workspace)
    _audit(
        db,
        workspace_id=workspace_id,
        actor_id=admin.id,
        action="settings.updated",
        detail=changes,
    )
    await db.commit()
    await db.refresh(workspace)
    return WorkspaceSettings.model_validate(workspace)


async def _require_workspace(db: AsyncSession, workspace_id: uuid.UUID) -> Workspace:
    """Load the caller's workspace row; the id was resolved from their account."""
    workspace = await db.get(Workspace, workspace_id)
    assert workspace is not None  # WorkspaceIdDep resolved it from the caller's row
    return workspace


def _apply_caps(workspace: Workspace, payload: WorkspaceSettingsUpdate) -> dict[str, Any]:
    """Set every cap the body supplied; return the changed keys, wire-named.

    ``model_fields_set`` is what separates "omitted" from "explicitly null" —
    the value alone cannot, and only an explicit ``null`` clears a cap.
    """
    changes: dict[str, Any] = {}
    for name, field in WorkspaceSettingsUpdate.model_fields.items():
        if name not in payload.model_fields_set:
            continue
        value = getattr(payload, name)
        if getattr(workspace, name) == value:
            continue
        setattr(workspace, name, value)
        # The audit row speaks the wire names, like every other detail payload.
        changes[field.alias or name] = value
    return changes


def _audit(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    actor_id: uuid.UUID,
    action: str,
    detail: dict[str, Any],
) -> None:
    """Stage one audit row for the caller to commit with its mutation."""
    db.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_user_id=actor_id,
            action=action,
            target_type="workspace",
            target_id=workspace_id,
            detail=detail,
        )
    )
