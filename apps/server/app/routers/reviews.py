"""Synchronous pre-flight validation endpoint (spec 10.3).

Runs the core :class:`PreflightService` and maps its outcome onto the wire
shape. Pre-flight never creates a session; it only reports what would be valid.
An unauthenticated or unconfigured workspace surfaces as a normal error
envelope, while expected validation failures land in ``invalid``/``notices``.

The coverage set the service checks carries repository **names**: a repository
the workspace parked (spec 10.1) is left out of it, which is the honest answer —
its pull requests are not reviewed — but the service can only say "not covered".
The app knows *why*, so :func:`explain_parked` names the parked repositories
before this route or session creation reports the refusal.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import (
    CurrentUserDep,
    DbSessionDep,
    PreflightServiceDep,
    WorkspaceIdDep,
)
from app.schemas import (
    PreflightRequest,
    PreflightResult,
    PrReference,
    RepositoryRef,
)
from slopolis_core.preflight.models import PreflightOutcome
from slopolis_core.preflight.models import PrReference as CorePrReference
from slopolis_db.models import Repository

__all__ = ["explain_parked", "router"]

router = APIRouter(prefix="/reviews", tags=["reviews"])

#: Core's notice for a link whose repository is outside the coverage set. The
#: app upgrades this one when the repository is actually parked; the string is
#: pinned because core owns the wording and offers no other handle on the case.
_UNCOVERED_NOTICE = (
    "Repository {full_name} is not covered by the GitHub App installation."
)


def _parked_notice(full_name: str) -> str:
    """The refusal a parked repository's link earns: why, and how to undo it."""
    return (
        f"Repository {full_name} is disabled in slopolis, so its pull requests "
        "are not reviewed. Enable it under Repositories to review them again."
    )


@router.post("/preflight")
async def preflight(
    body: PreflightRequest,
    user: CurrentUserDep,
    service: PreflightServiceDep,
    db: DbSessionDep,
    workspace_id: WorkspaceIdDep,
) -> PreflightResult:
    """Validate a New Review submission and report valid/invalid targets."""
    from slopolis_core.preflight.models import PreflightRequest as CoreRequest

    outcome = await service.run(
        CoreRequest(pr_urls=body.pr_urls),
        user_login=user.handle,
    )
    return _to_result(await explain_parked(db, workspace_id, outcome))


async def explain_parked(
    db: AsyncSession, workspace_id: uuid.UUID, outcome: PreflightOutcome
) -> PreflightOutcome:
    """Replace the "not covered" refusal of a parked repository with its reason.

    Only repositories this workspace holds a row for can be parked, so a link to
    somewhere else keeps core's notice untouched. Every caller that reports the
    refusal — this route's notices, session creation's 422 detail — rewrites the
    outcome here first, so the reason it names is the real one.
    """
    if not outcome.notices:
        return outcome
    parked = (
        await db.scalars(
            select(Repository.full_name).where(
                Repository.workspace_id == workspace_id,
                Repository.enabled.is_(False),
            )
        )
    ).all()
    if not parked:
        return outcome
    reasons = {
        _UNCOVERED_NOTICE.format(full_name=full_name): _parked_notice(full_name)
        for full_name in parked
    }
    notices = [reasons.get(notice, notice) for notice in outcome.notices]
    if notices == outcome.notices:
        return outcome
    return outcome.model_copy(update={"notices": notices})


def _to_result(outcome: PreflightOutcome) -> PreflightResult:
    """Map the core outcome onto the wire result."""
    return PreflightResult(
        valid=[_to_reference(reference) for reference in outcome.valid],
        invalid=outcome.invalid,
        notices=outcome.notices,
    )


def _to_reference(reference: CorePrReference) -> PrReference:
    """Map one resolved core PR reference onto the wire reference."""
    return PrReference(
        url=reference.url,
        repository=RepositoryRef(
            id=reference.repository.id,
            full_name=reference.repository.full_name,
            private=reference.repository.private,
            default_branch=reference.repository.default_branch,
        ),
        number=reference.number,
        title=reference.title,
    )
