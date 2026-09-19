"""Synchronous pre-flight validation endpoint (spec 10.3).

Runs the core :class:`PreflightService` and maps its outcome onto the wire
shape. Pre-flight never creates a session; it only reports what would be valid.
An unauthenticated or unconfigured workspace surfaces as a normal error
envelope, while expected validation failures land in ``invalid``/``notices``.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.deps import CurrentUserDep, PreflightServiceDep
from app.schemas import (
    PreflightRequest,
    PreflightResult,
    PrReference,
    RepositoryRef,
)
from slopolis_core.preflight.models import PreflightOutcome
from slopolis_core.preflight.models import PrReference as CorePrReference

__all__ = ["router"]

router = APIRouter(prefix="/reviews", tags=["reviews"])


@router.post("/preflight")
async def preflight(
    body: PreflightRequest,
    user: CurrentUserDep,
    service: PreflightServiceDep,
) -> PreflightResult:
    """Validate a New Review submission and report valid/invalid targets."""
    from slopolis_core.preflight.models import PreflightRequest as CoreRequest

    outcome = await service.run(
        CoreRequest(pr_urls=body.pr_urls),
        user_login=user.handle,
    )
    return _to_result(outcome)


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
