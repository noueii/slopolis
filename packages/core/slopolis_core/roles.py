"""The model-role vocabulary shared by the workspace config and the harness.

A *model role* is the only name a review agent, an orchestrator, or a repo
config may use to reach a model: the workspace resolves it through
``ModelAssignment`` (spec 10.2), so no model name ever appears in code or in a
repository's ``.codereview.yml``.

Kept in its own module because three layers need the same list and may not
depend on each other: the API that edits assignments, the worker that resolves
the v1 reviewer, and the harness registry that binds each built-in agent to a
role.
"""

from __future__ import annotations

__all__ = ["ASSIGNABLE_ROLES", "HARNESS_ROLES", "REVIEW_ROLE", "is_assignable_role"]

#: The v1 built-in reviewer's role (spec 10.6) — one agent, single pass.
REVIEW_ROLE = "review"

#: Harness V1 roles (spec v2 §3): orchestrators plus the reviewer aspects.
HARNESS_ROLES: tuple[str, ...] = (
    "harness.orchestrator",
    "review.fast",
    "review.specialist",
    "review.security",
    "review.tests",
)

#: Every role a workspace may bind to a model, in display order.
ASSIGNABLE_ROLES: tuple[str, ...] = (REVIEW_ROLE, *HARNESS_ROLES)


def is_assignable_role(role: str) -> bool:
    """Return whether ``role`` is a role the workspace may assign a model to."""
    return role in ASSIGNABLE_ROLES
