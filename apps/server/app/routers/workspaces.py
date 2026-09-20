"""Workspace onboarding: create a workspace, or list the ones you belong to.

v1 is self-hosted and single-tenant, but signing in no longer hands every new
account a workspace. A user with none lands on the onboarding gate and either
creates one (becoming its admin) or waits to be invited — the invitation
mechanism itself is deferred with multi-tenancy (spec §5, Phase 5).
"""

from __future__ import annotations

import re

from fastapi import APIRouter, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import CurrentUserDep, DbSessionDep
from app.errors import ApiError
from app.schemas import CreateWorkspaceRequest, WorkspaceListResponse, WorkspaceRef
from app.serializers import workspace_ref
from slopolis_db.models import User, Workspace

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
