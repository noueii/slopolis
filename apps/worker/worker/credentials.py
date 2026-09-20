"""Resolve the workspace credential that serves a model (spec 10.2).

A workspace credential is only BYOK if the calls actually use it, so the
resolution follows the model: ``model_catalog.credential_id`` names the
``provider_credentials`` row, and this module turns that row into a base URL plus
the key decrypted from the vault — or ``None`` when the model has no usable
credential, which leaves the process-level gateway as the fallback.

Nothing here is fatal: an unset ``ENCRYPTION_KEY`` and a blob this master key
cannot open both mean *no usable credential*, logged once per process for the
former and once per encounter for the latter, never echoed back with the key.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from functools import lru_cache

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from slopolis_core.settings import get_settings
from slopolis_core.vault import SecretVault, VaultDecryptError, VaultNotConfigured
from slopolis_db.models import ModelCatalog, ProviderCredential
from worker.errors import PermanentTargetError

__all__ = [
    "ModelNotConfiguredError",
    "ResolvedCredential",
    "credential_for_model",
    "get_credential_vault",
    "missing_credential_message",
]

_LOG = logging.getLogger("worker.credentials")


@dataclass(frozen=True, slots=True)
class ResolvedCredential:
    """The base URL and the decrypted key a model's calls must use."""

    base_url: str
    #: Kept out of the repr: a credential's key must never reach a log line or a
    #: traceback, and ``f"{credential}"`` is exactly how it would.
    api_key: str = field(repr=False)
    credential_id: uuid.UUID


@lru_cache
def get_credential_vault() -> SecretVault | None:
    """Return the process-wide vault, or ``None`` when ``ENCRYPTION_KEY`` is unset.

    Without a vault no stored key can be read, so only the fallback gateway is
    available: a missing capability rather than a failure, worth one warning line
    per process (which the cache makes literal). Tests that change
    ``ENCRYPTION_KEY`` clear it with ``get_credential_vault.cache_clear()``.
    """
    try:
        return SecretVault.from_settings()
    except VaultNotConfigured as exc:
        _LOG.warning("Workspace credentials are unusable: %s", exc)
        return None


async def credential_for_model(
    db: AsyncSession, *, workspace_id: uuid.UUID, model_id: str
) -> ResolvedCredential | None:
    """Return the credential linked to ``model_id``, or ``None`` when unusable.

    ``None`` covers every reason a stored credential cannot serve a call: the
    model is not in the workspace catalog, its catalog row links no credential,
    the credential is disabled, its row is gone, the vault is unconfigured, or the
    blob does not open with this master key. The caller falls back to the process
    gateway, and only fails — naming the model — when that is absent too.
    """
    catalog = (
        await db.execute(
            select(ModelCatalog).where(
                ModelCatalog.workspace_id == workspace_id,
                ModelCatalog.model_id == model_id,
            )
        )
    ).scalars().first()
    if catalog is None or catalog.credential_id is None:
        return None

    credential = await db.get(ProviderCredential, catalog.credential_id)
    if credential is None or not credential.enabled:
        return None

    vault = get_credential_vault()
    if vault is None:
        return None
    try:
        api_key = vault.open(credential.encrypted_api_key)
    except VaultDecryptError as exc:
        _LOG.warning(
            "Workspace credential cannot be decrypted: %s",
            exc,
            extra={"credential_id": str(credential.id), "model_id": model_id},
        )
        return None

    # A credential that names no URL rides the configured gateway host, exactly
    # the way the provider probe resolves one (spec 10.2 §Which credential a model
    # call uses): one base URL, without ``/v1``, serves both the probe and the call.
    base_url = credential.base_url or get_settings().litellm_base_url
    return ResolvedCredential(
        base_url=base_url, api_key=api_key, credential_id=credential.id
    )


def missing_credential_message(model_id: str) -> str:
    """Explain a model nothing can serve, in the vocabulary the server uses.

    Both ways out are named, and neither path is a silent default, so a user who
    reads this message knows whether to link a credential to the model or to give
    the deployment a gateway.
    """
    return (
        f"model {model_id} has no usable credential and the model gateway is not "
        f"configured; link a credential to {model_id} or set LITELLM_BASE_URL and "
        f"LITELLM_MASTER_KEY on the worker"
    )


class ModelNotConfiguredError(PermanentTargetError):
    """Neither a workspace credential nor the process gateway can serve the model."""
