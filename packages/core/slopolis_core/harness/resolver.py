"""Model resolution for the harness (spec v2 §3, spec 10.2).

An agent never names a model: it carries a *model role*, and the workspace
resolves that role through ``ModelAssignment`` to a provider plus model from the
workspace catalog. This module is that seam and nothing more — the port, its
typed failure, and the in-memory double the tests script. The real resolver
lives where the DB lives, so ``core`` keeps no database import.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

__all__ = ["ModelChoice", "ModelResolutionError", "ModelResolver", "StaticResolver"]


class ModelChoice(BaseModel):
    """The concrete model a role resolved to: a catalog id and its provider."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    model_id: str
    provider: str


class ModelResolutionError(RuntimeError):
    """No model could be resolved for a role (no assignment and no default)."""

    def __init__(self, model_role: str) -> None:
        super().__init__(f"no model resolves for model role {model_role!r}")
        self.model_role = model_role


@runtime_checkable
class ModelResolver(Protocol):
    """Resolves a model role to the model a run must be sent to (spec §3)."""

    async def resolve(self, model_role: str) -> ModelChoice:
        """Return the model bound to ``model_role``, or raise :class:`ModelResolutionError`."""
        ...


class StaticResolver:
    """Resolver backed by a fixed role → model map; used by tests and preflight."""

    def __init__(self, choices: Mapping[str, ModelChoice]) -> None:
        self._choices = dict(choices)

    @property
    def choices(self) -> Mapping[str, ModelChoice]:
        """The configured map, as a read-only view."""
        return self._choices

    async def resolve(self, model_role: str) -> ModelChoice:
        """Return the configured choice, raising when the role has none."""
        try:
            return self._choices[model_role]
        except KeyError as exc:
            raise ModelResolutionError(model_role) from exc
