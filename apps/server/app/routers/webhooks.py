"""GitHub webhook ingest (spec 10.1 §Webhooks).

``POST /api/github/webhook`` is how GitHub keeps this deployment's installations
and repositories current after the one-off setup callback, so the app stops
depending on the user revisiting the install page.

Every delivery is authenticated **before** it is parsed: the raw body's
``X-Hub-Signature-256`` is checked against ``GITHUB_WEBHOOK_SECRET``
(:func:`app.services.webhook_sync.signature_matches`). An unset secret, a missing
header, and a digest that does not match the body are all the same **401**, with
nothing parsed and nothing written — there is no unauthenticated mode, and the
legacy SHA-1 header is not accepted either.

Dispatch is on ``X-GitHub-Event``; the split between what is applied and what is
ignored on purpose:

==============================  ===============================================
event                           what the delivery does
==============================  ===============================================
``installation``                ``created``/``unsuspend`` upsert the
                                installation row (id, account login and type)
                                and reconnect its repositories; ``deleted`` and
                                ``suspend`` mark every one of them unusable,
                                keeping the rows as history
``installation_repositories``   ``repositories_added`` are upserted and
                                reconnected, ``repositories_removed`` are marked
                                unusable
``repository``                  ``renamed``/``transferred`` update ``full_name``
                                (and ``private``, and the installation on a
                                transfer); ``deleted`` marks the row unusable
``ping``                        answered, nothing written
anything else                   **ignored** — accepted with 202, because a
                                delivery GitHub starts sending before we handle
                                it must not become a retry storm
==============================  ===============================================

A payload naming an installation this deployment does not know is ignored the
same way, so GitHub's data can never turn a delivery into a 500.

Triggering a review from a comment stays Phase 2: this endpoint only synchronizes
installations and repositories, and never creates a session.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, cast

from fastapi import APIRouter, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import AppSettingsDep, DbSessionDep
from app.errors import ApiError
from app.services.webhook_sync import (
    signature_matches,
    sync_installation_event,
    sync_installation_repositories_event,
    sync_repository_event,
)

__all__ = ["router"]

router = APIRouter(prefix="/github", tags=["github"])

#: The HMAC of the raw body, ``sha256=<hex digest>``.
_SIGNATURE_HEADER = "X-Hub-Signature-256"
#: The event that triggered the delivery.
_EVENT_HEADER = "X-GitHub-Event"

#: One handler per event this endpoint applies; anything else is accepted and
#: ignored, so the table is the whole list of events it writes for.
_HANDLERS: dict[
    str, Callable[[AsyncSession, Mapping[str, Any]], Awaitable[bool]]
] = {
    "installation": sync_installation_event,
    "installation_repositories": sync_installation_repositories_event,
    "repository": sync_repository_event,
}


@router.post("/webhook", status_code=status.HTTP_202_ACCEPTED)
async def github_webhook(
    request: Request,
    db: DbSessionDep,
    settings: AppSettingsDep,
) -> dict[str, Any]:
    """Verify one delivery, then apply its row writes; 202 once accepted."""
    body = await request.body()
    secret = settings.core.github_webhook_secret
    if not signature_matches(
        secret=secret,
        body=body,
        signature=request.headers.get(_SIGNATURE_HEADER),
    ):
        raise ApiError(
            status.HTTP_401_UNAUTHORIZED,
            "invalid_signature",
            "The webhook signature could not be verified.",
            detail=(
                "GITHUB_WEBHOOK_SECRET is not configured, so no delivery can be authenticated."
                if not secret
                else (
                    "The X-Hub-Signature-256 header is missing or does not match "
                    "the request body."
                )
            ),
        )

    event = request.headers.get(_EVENT_HEADER, "")
    payload = _decode_body(body)
    handler = _HANDLERS.get(event)
    applied = await handler(db, payload) if handler is not None else False
    return {"event": event, "applied": applied}


def _decode_body(body: bytes) -> dict[str, Any]:
    """Parse a verified body as a JSON object, or fail with a typed 400."""
    try:
        payload: object = json.loads(body)
    except ValueError as exc:
        raise ApiError(
            400,
            "invalid_payload",
            "The webhook body is not valid JSON.",
            detail=str(exc),
        ) from exc
    if not isinstance(payload, dict):
        raise ApiError(
            400,
            "invalid_payload",
            "The webhook body must be a JSON object.",
        )
    return cast("dict[str, Any]", payload)
