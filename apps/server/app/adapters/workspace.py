"""``preflight.ports.WorkspaceConfigProvider`` implemented from the database.

Reads the workspace's model assignment, model catalog, and credential rows to
answer the three questions pre-flight asks: which model is assigned, is a
credential ready, and what is the workspace default. All queries are scoped to
a single workspace id resolved once per request.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from slopolis_db.models import ModelAssignment, ModelCatalog, ProviderCredential

__all__ = ["WorkspaceConfigAdapter", "resolve_default_workspace_id"]

_REVIEW_ROLE = "review"


class WorkspaceConfigAdapter:
    """Workspace model assignment and credential state, backed by the DB."""

    def __init__(self, db: AsyncSession, workspace_id: uuid.UUID) -> None:
        self._db = db
        self._workspace_id = workspace_id

    async def default_model(self) -> tuple[str, str] | None:
        """Return ``(model_id, provider)`` for the workspace default, or ``None``."""
        assignment = await self._assignment(_REVIEW_ROLE)
        if assignment is not None:
            resolved = await self._model_for_assignment(assignment)
            if resolved is not None:
                return resolved
        catalog = await self._db.scalar(
            select(ModelCatalog)
            .where(ModelCatalog.workspace_id == self._workspace_id)
            .order_by(ModelCatalog.created_at)
            .limit(1)
        )
        if catalog is None:
            return None
        return catalog.model_id, catalog.provider

    async def credential_ready(self) -> bool:
        """Return whether the workspace holds at least one enabled credential."""
        credential = await self._db.scalar(
            select(ProviderCredential.id)
            .where(
                ProviderCredential.workspace_id == self._workspace_id,
                ProviderCredential.enabled.is_(True),
            )
            .limit(1)
        )
        return credential is not None

    async def model_assigned(self, role: str) -> tuple[str, str] | None:
        """Return ``(model_id, provider)`` assigned to ``role``, or ``None``."""
        assignment = await self._assignment(role)
        if assignment is None:
            return None
        return await self._model_for_assignment(assignment)

    async def _assignment(self, role: str) -> ModelAssignment | None:
        """Fetch the assignment row for ``role`` in this workspace."""
        return await self._db.scalar(
            select(ModelAssignment).where(
                ModelAssignment.workspace_id == self._workspace_id,
                ModelAssignment.role == role,
            )
        )

    async def _model_for_assignment(
        self, assignment: ModelAssignment
    ) -> tuple[str, str] | None:
        """Resolve an assignment to ``(model_id, provider)`` via its catalog row."""
        if assignment.model_catalog_id is not None:
            catalog = await self._db.get(ModelCatalog, assignment.model_catalog_id)
        elif assignment.model_id is not None:
            catalog = await self._db.scalar(
                select(ModelCatalog).where(
                    ModelCatalog.workspace_id == self._workspace_id,
                    ModelCatalog.model_id == assignment.model_id,
                )
            )
        else:
            return None
        if catalog is None:
            return None
        return catalog.model_id, catalog.provider


async def resolve_default_workspace_id(db: AsyncSession) -> uuid.UUID | None:
    """Return the first workspace id, or ``None`` when the DB is empty.

    v1 is self-hosted and single-tenant, so the earliest workspace is the
    default tenant every request operates against.
    """
    from slopolis_db.models import Workspace

    return await db.scalar(select(Workspace.id).order_by(Workspace.created_at).limit(1))
