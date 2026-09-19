"""Static catalog endpoints: models and review presets.

Model catalog and presets are workspace configuration that becomes dynamic in
later phases; the v1 wire contract only needs stable defaults, so these are
served from constants. ``GET /api/models`` still prefer a DB-backed default
when one exists, so a configured workspace sees its own assignment.
"""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import select

from app.deps import DbSessionDep, WorkspaceIdDep
from app.schemas import (
    ModelCatalog,
    ModelOption,
    ReviewPreset,
    ReviewPresetCatalog,
)

__all__ = ["router"]

router = APIRouter(tags=["catalog"])

#: Fallback catalog shown before a workspace configures providers.
_FALLBACK_MODELS: tuple[ModelOption, ...] = (
    ModelOption(id="gpt-4o", provider="OpenAI"),
    ModelOption(id="gpt-4o-mini", provider="OpenAI"),
    ModelOption(id="claude-sonnet-4", provider="Anthropic"),
    ModelOption(id="claude-opus-4", provider="Anthropic"),
    ModelOption(id="gemini-2.5-pro", provider="Google"),
)

DEFAULT_MODEL_ID = "claude-sonnet-4"
DEFAULT_PROVIDER = "Anthropic"

#: Built-in review presets (spec: future orchestration tab).
_PRESETS: tuple[ReviewPreset, ...] = (
    ReviewPreset(
        id="default",
        name="Default",
        description="Balanced built-in review across every target.",
    ),
    ReviewPreset(
        id="security",
        name="Security audit",
        description="Prioritizes auth, injection, and secret-handling risks.",
    ),
    ReviewPreset(
        id="performance",
        name="Performance review",
        description="Focuses on hot paths, N+1s, and allocation pressure.",
    ),
    ReviewPreset(
        id="tests",
        name="Test coverage",
        description="Flags untested branches and missing edge cases.",
    ),
)


@router.get("/models")
async def list_models(db: DbSessionDep, workspace_id: WorkspaceIdDep) -> ModelCatalog:
    """Return the workspace model catalog with its default selection."""
    from slopolis_db.models import ModelCatalog as ModelCatalogRow

    rows = list(
        (
            await db.scalars(
                select(ModelCatalogRow)
                .where(ModelCatalogRow.workspace_id == workspace_id)
                .order_by(ModelCatalogRow.created_at)
            )
        ).all()
    )
    if not rows:
        return ModelCatalog(
            default_model_id=DEFAULT_MODEL_ID,
            default_provider=DEFAULT_PROVIDER,
            models=list(_FALLBACK_MODELS),
        )
    options = [ModelOption(id=row.model_id, provider=row.provider) for row in rows]
    return ModelCatalog(
        default_model_id=rows[0].model_id,
        default_provider=rows[0].provider,
        models=options,
    )


@router.get("/presets")
async def list_presets() -> ReviewPresetCatalog:
    """Return the built-in review preset catalog."""
    return ReviewPresetCatalog(default_preset_id="default", presets=list(_PRESETS))
