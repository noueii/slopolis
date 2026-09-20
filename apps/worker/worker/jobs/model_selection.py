"""Resolve the model + provider for a review target (spec 10.2 / 10.5).

Resolution order, no model names hardcoded:

1. the workspace's ``review`` role assignment (its explicit ``model_id`` or its
   linked catalog entry),
2. any catalog model for the workspace (the catalog default),
3. the model/provider recorded on the session at submit time.

The resolved model carries the credential that serves it (its base URL and the
key decrypted from the vault), so the model a call is about is what decides the
gateway it calls — see :mod:`worker.credentials`.

If none resolve, :class:`ModelResolutionError` is raised so the target fails
loudly rather than guessing.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from slopolis_db.models import ModelAssignment, ModelCatalog
from worker.credentials import ResolvedCredential, credential_for_model

__all__ = ["REVIEW_ROLE", "ModelResolutionError", "ResolvedModel", "resolve_model"]

#: Workspace role the v1 built-in reviewer uses.
REVIEW_ROLE = "review"


class ModelResolutionError(RuntimeError):
    """No model could be resolved for the workspace."""


class ResolvedModel:
    """A model id paired with the provider and credential that serve it."""

    def __init__(
        self,
        model_id: str,
        provider: str,
        credential: ResolvedCredential | None = None,
    ) -> None:
        self.model_id = model_id
        self.provider = provider
        #: ``None`` means the model has no usable workspace credential, so its
        #: calls fall back to the process-level gateway.
        self.credential = credential


async def resolve_model(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    fallback_model: str,
    fallback_provider: str,
) -> ResolvedModel:
    """Resolve the reviewer model for ``workspace_id``.

    Falls back to the session's recorded model/provider before raising.
    """
    resolved = await _resolve(
        db,
        workspace_id=workspace_id,
        fallback_model=fallback_model,
        fallback_provider=fallback_provider,
    )
    # The credential follows the model, not the workspace: whatever the model a
    # call is about, that is the key it uses (spec 10.2).
    resolved.credential = await credential_for_model(
        db, workspace_id=workspace_id, model_id=resolved.model_id
    )
    return resolved


async def _resolve(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    fallback_model: str,
    fallback_provider: str,
) -> ResolvedModel:
    """Resolve the model id and provider, without touching the credential."""
    assignment = (
        await db.execute(
            select(ModelAssignment).where(
                ModelAssignment.workspace_id == workspace_id,
                ModelAssignment.role == REVIEW_ROLE,
            )
        )
    ).scalar_one_or_none()

    if assignment is not None:
        resolved = await _from_assignment(db, assignment)
        if resolved is not None:
            return resolved

    catalog = (
        await db.execute(
            select(ModelCatalog)
            .where(ModelCatalog.workspace_id == workspace_id)
            .order_by(ModelCatalog.created_at)
        )
    ).scalars().first()
    if catalog is not None:
        return ResolvedModel(catalog.model_id, catalog.provider)

    if fallback_model:
        return ResolvedModel(fallback_model, fallback_provider)

    raise ModelResolutionError("No review model is assigned and no catalog model exists")


async def _from_assignment(
    db: AsyncSession, assignment: ModelAssignment
) -> ResolvedModel | None:
    """Resolve an assignment via its catalog link or its explicit model id."""
    if assignment.model_catalog_id is not None:
        catalog = await db.get(ModelCatalog, assignment.model_catalog_id)
        if catalog is not None:
            return ResolvedModel(catalog.model_id, catalog.provider)

    if assignment.model_id:
        provider = assignment.model_id.split("/", 1)[0]
        return ResolvedModel(assignment.model_id, provider)

    return None
