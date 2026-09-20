"""``preflight.ports.WorkspaceConfigProvider`` implemented from the database.

Reads the workspace's model assignment, model catalog, credential rows, and the
repository access override to answer the questions pre-flight asks: which model
is assigned, is a credential ready, what is the workspace default, what access
does one repository require, and which credential serves a model. All queries are
scoped to a single workspace id resolved once per request.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.live_check import ModelCredential
from slopolis_core.vault import SecretVault
from slopolis_db.models import ModelAssignment, ModelCatalog, ProviderCredential, Repository

__all__ = ["WorkspaceConfigAdapter"]

_REVIEW_ROLE = "review"

#: ``repositories.required_access`` value meaning "apply the spec rule".
_DEFAULT_ACCESS = "default"


class WorkspaceConfigAdapter:
    """Workspace model assignment, credential state, and access policy, backed by the DB."""

    def __init__(
        self,
        db: AsyncSession,
        workspace_id: uuid.UUID,
        *,
        vault: SecretVault | None = None,
        default_base_url: str = "",
    ) -> None:
        """Take the session and the two things only the caller can supply.

        ``vault`` opens a stored key, so it comes from the caller rather than from
        settings: a process without ``ENCRYPTION_KEY`` has no vault, and there the
        stored credentials are simply unusable — only the gateway fallback remains
        (spec 10.2). ``default_base_url`` is the deployment's gateway base URL,
        applied to a credential that stores none; the *Test connection* probe
        resolves the same way, so the URL it tested and the URL a call uses agree.
        """
        self._db = db
        self._workspace_id = workspace_id
        self._vault = vault
        self._default_base_url = default_base_url

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

    async def credential_for_model(self, model_id: str) -> ModelCredential | None:
        """Return the credential serving ``model_id``, or ``None`` when none is usable.

        Pre-flight resolves the review role to a model id through
        :meth:`model_assigned`/:meth:`default_model`; this answers the credential
        side of that same model, so a call runs on the key and base URL the
        screen's *Test connection* exercised (spec 10.2).

        ``None`` means the caller must fall back to the process gateway: the model
        has no catalog row here, its row is linked to no credential, the
        credential is disabled, the process has no vault to open the key with, or
        neither the credential nor the deployment names a base URL. A blob this
        master key cannot open raises instead — a rotated key is a configuration
        error, not an absent credential.
        """
        catalog = await self._db.scalar(
            select(ModelCatalog).where(
                ModelCatalog.workspace_id == self._workspace_id,
                ModelCatalog.model_id == model_id,
            )
        )
        if catalog is None or catalog.credential_id is None:
            return None
        credential = await self._db.get(ProviderCredential, catalog.credential_id)
        if credential is None or not credential.enabled or self._vault is None:
            return None
        base_url = credential.base_url or self._default_base_url
        if not base_url:
            return None
        return ModelCredential(
            base_url=base_url.rstrip("/"),
            api_key=self._vault.open(credential.encrypted_api_key),
            key_last4=credential.key_last4,
        )

    async def required_access(self, repo_full_name: str) -> str | None:
        """Return the repository's access override, or ``None`` for the spec rule.

        ``None`` covers both "no override stored" and "the workspace holds no
        row for this repository": either way pre-flight applies the default rule,
        and only a stored ``read``/``write`` changes what is required.
        """
        override = await self._db.scalar(
            select(Repository.required_access).where(
                Repository.workspace_id == self._workspace_id,
                Repository.full_name == repo_full_name,
            )
        )
        if override is None or override == _DEFAULT_ACCESS:
            return None
        return override

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
