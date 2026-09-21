"""GitHub webhook ingest (spec 10.1 §Webhooks).

The endpoint is the deployment's only GitHub-initiated write path, so half of
these tests are about the gate: a delivery that is not signed with
``GITHUB_WEBHOOK_SECRET`` must be refused *before* anything is parsed, leaving
the database byte-for-byte as it was. The rest pin what each handled event does
to the rows, including that replaying one changes nothing.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select

from slopolis_core.settings import get_settings
from slopolis_db.models import GitHubInstallation, Repository

from .conftest import ApiHarness

WEBHOOK_PATH = "/api/github/webhook"
#: The secret the deployment is given for a test, and the one GitHub signs with.
WEBHOOK_SECRET = "a-webhook-secret-for-tests"
#: What ``conftest`` seeds: one installation on ``acme`` with one repository.
SEEDED_INSTALLATION_ID = 555
SEEDED_REPOSITORY_ID = 4242
SEEDED_FULL_NAME = "acme/api"
#: An installation and repository GitHub knows and this workspace does not.
NEW_INSTALLATION_ID = 777
NEW_REPOSITORY_ID = 6001
#: An installation id no workspace has ever recorded.
UNKNOWN_INSTALLATION_ID = 999_999


@pytest.fixture
def webhook_secret(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """Give the deployment a webhook secret for one test.

    Core settings are cached process-wide, so the cache is dropped on both sides
    of the environment patch — the same pattern the OAuth credentials use — and
    every other test keeps the unconfigured default.
    """
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", WEBHOOK_SECRET)
    try:
        get_settings.cache_clear()
        yield WEBHOOK_SECRET
    finally:
        get_settings.cache_clear()


def signature(body: bytes, secret: str = WEBHOOK_SECRET) -> str:
    """The ``X-Hub-Signature-256`` value GitHub would send for ``body``."""
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


async def deliver(
    harness: ApiHarness, event: str, payload: dict[str, Any]
) -> httpx.Response:
    """POST one correctly signed delivery, exactly as GitHub sends it."""
    body = json.dumps(payload).encode()
    return await harness.client.post(
        WEBHOOK_PATH,
        content=body,
        headers={"X-GitHub-Event": event, "X-Hub-Signature-256": signature(body)},
    )


def installation_payload(
    action: str,
    *,
    installation_id: int = SEEDED_INSTALLATION_ID,
    login: str = "acme",
) -> dict[str, Any]:
    """An ``installation`` delivery for one account."""
    return {
        "action": action,
        "installation": {
            "id": installation_id,
            "account": {"login": login, "type": "Organization"},
        },
    }


def repository_payload(
    action: str, *, changes: dict[str, Any] | None = None, **repository: Any
) -> dict[str, Any]:
    """A ``repository`` delivery for the seeded repository."""
    item: dict[str, Any] = {
        "id": SEEDED_REPOSITORY_ID,
        "full_name": SEEDED_FULL_NAME,
        "private": False,
        "default_branch": "main",
    }
    item.update(repository)
    payload: dict[str, Any] = {
        "action": action,
        "installation": {"id": SEEDED_INSTALLATION_ID},
        "repository": item,
    }
    if changes is not None:
        payload["changes"] = changes
    return payload


async def installations(session_factory: Any) -> list[GitHubInstallation]:
    """Every installation row, ordered by GitHub's id."""
    async with session_factory() as session:
        return list(
            (
                await session.scalars(
                    select(GitHubInstallation).order_by(GitHubInstallation.installation_id)
                )
            ).all()
        )


async def repositories(session_factory: Any) -> list[Repository]:
    """Every repository row, ordered by name."""
    async with session_factory() as session:
        return list(
            (
                await session.scalars(select(Repository).order_by(Repository.full_name))
            ).all()
        )


async def row_counts(session_factory: Any) -> tuple[int, int]:
    """``(installations, repositories)`` — the whole footprint a delivery can leave."""
    async with session_factory() as session:
        return (
            await session.scalar(select(func.count()).select_from(GitHubInstallation)) or 0,
            await session.scalar(select(func.count()).select_from(Repository)) or 0,
        )


# --- the signature gate -----------------------------------------------------


async def test_a_delivery_without_a_signature_is_refused(
    seeded: Any, build_harness: Any, session_factory: Any, webhook_secret: str
) -> None:
    # Given a configured deployment and a delivery GitHub would have signed
    harness: ApiHarness = await build_harness(user_id=seeded.user_id)
    body = json.dumps(installation_payload("created", installation_id=NEW_INSTALLATION_ID)).encode()

    # When it arrives with no signature header at all
    response = await harness.client.post(
        WEBHOOK_PATH, content=body, headers={"X-GitHub-Event": "installation"}
    )

    # Then it is refused, and the payload is not reflected in any row
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_signature"
    assert await row_counts(session_factory) == (1, 1)


async def test_a_delivery_signed_with_another_secret_is_refused(
    seeded: Any, build_harness: Any, session_factory: Any, webhook_secret: str
) -> None:
    # Given a delivery signed with a secret that is not the deployment's
    harness: ApiHarness = await build_harness(user_id=seeded.user_id)
    payload = repository_payload("renamed", full_name="acme/attacker")
    body = json.dumps(payload).encode()

    # When it arrives
    response = await harness.client.post(
        WEBHOOK_PATH,
        content=body,
        headers={
            "X-GitHub-Event": "repository",
            "X-Hub-Signature-256": signature(body, "some-other-secret"),
        },
    )

    # Then it is refused and the repository it names is untouched
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_signature"
    stored = await repositories(session_factory)
    assert [row.full_name for row in stored] == [SEEDED_FULL_NAME]
    assert stored[0].connected is True


async def test_a_tampered_body_is_refused(
    seeded: Any, build_harness: Any, session_factory: Any, webhook_secret: str
) -> None:
    # Given a signature that authenticates a different body than the one sent
    harness: ApiHarness = await build_harness(user_id=seeded.user_id)
    signed = json.dumps(
        installation_payload("created", installation_id=NEW_INSTALLATION_ID)
    ).encode()
    tampered = json.dumps(repository_payload("deleted")).encode()

    # When the tampered body carries that signature
    response = await harness.client.post(
        WEBHOOK_PATH,
        content=tampered,
        headers={
            "X-GitHub-Event": "repository",
            "X-Hub-Signature-256": signature(signed),
        },
    )

    # Then it is refused rather than applied
    assert response.status_code == 401
    assert await row_counts(session_factory) == (1, 1)
    stored = await repositories(session_factory)
    assert stored[0].connected is True


async def test_a_deployment_without_a_secret_refuses_every_delivery(
    seeded: Any, build_harness: Any, session_factory: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given a deployment with no GITHUB_WEBHOOK_SECRET, so nothing can be verified
    monkeypatch.delenv("GITHUB_WEBHOOK_SECRET", raising=False)
    get_settings.cache_clear()
    harness: ApiHarness = await build_harness(user_id=seeded.user_id)
    payload = installation_payload("created", installation_id=NEW_INSTALLATION_ID)
    body = json.dumps(payload).encode()

    # When a delivery arrives with a signature of any kind
    response = await harness.client.post(
        WEBHOOK_PATH,
        content=body,
        headers={
            "X-GitHub-Event": "installation",
            "X-Hub-Signature-256": signature(body),
        },
    )

    # Then there is no unauthenticated mode to fall into
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_signature"
    assert await row_counts(session_factory) == (1, 1)


async def test_a_verified_body_must_be_a_json_object(
    seeded: Any, build_harness: Any, session_factory: Any, webhook_secret: str
) -> None:
    # Given a correctly signed body that is not a JSON object
    harness: ApiHarness = await build_harness(user_id=seeded.user_id)
    body = b"not json at all"

    # When it arrives
    response = await harness.client.post(
        WEBHOOK_PATH,
        content=body,
        headers={
            "X-GitHub-Event": "installation",
            "X-Hub-Signature-256": signature(body),
        },
    )

    # Then the caller gets a typed 400 rather than a 500
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_payload"
    assert await row_counts(session_factory) == (1, 1)


# --- ping and unhandled events ----------------------------------------------


async def test_ping_is_accepted_and_writes_nothing(
    seeded: Any, build_harness: Any, session_factory: Any, webhook_secret: str
) -> None:
    # Given a configured deployment
    harness: ApiHarness = await build_harness(user_id=seeded.user_id)

    # When GitHub's ping arrives
    response = await deliver(harness, "ping", {"zen": "Non-blocking is better than blocking."})

    # Then it is acknowledged without touching a row
    assert response.status_code == 202
    assert response.json() == {"event": "ping", "applied": False}
    assert await row_counts(session_factory) == (1, 1)


async def test_an_unhandled_event_is_accepted_and_ignored(
    seeded: Any, build_harness: Any, session_factory: Any, webhook_secret: str
) -> None:
    # Given a configured deployment subscribed to more events than this endpoint
    # handles (spec 10.1: comment triggers are Phase 2)
    harness: ApiHarness = await build_harness(user_id=seeded.user_id)

    # When one of them is delivered
    response = await deliver(harness, "issue_comment", {"action": "created", "comment": {"id": 1}})

    # Then it is accepted so GitHub stops redelivering it, and nothing is written
    assert response.status_code == 202
    assert response.json() == {"event": "issue_comment", "applied": False}
    assert await row_counts(session_factory) == (1, 1)


# --- installation -----------------------------------------------------------


async def test_a_new_installation_is_recorded_once_by_replaying_the_delivery(
    seeded: Any, build_harness: Any, session_factory: Any, webhook_secret: str
) -> None:
    # Given a delivery for an installation the workspace has never seen
    harness: ApiHarness = await build_harness(user_id=seeded.user_id)
    payload = installation_payload("created", installation_id=NEW_INSTALLATION_ID, login="beta")

    # When it is delivered twice, as GitHub does when the first response is lost
    first = await deliver(harness, "installation", payload)
    second = await deliver(harness, "installation", payload)

    # Then both are accepted and the account is recorded exactly once
    assert first.status_code == 202
    assert first.json() == {"event": "installation", "applied": True}
    assert second.status_code == 202
    stored = await installations(session_factory)
    assert [(row.installation_id, row.account_login) for row in stored] == [
        (SEEDED_INSTALLATION_ID, "acme"),
        (NEW_INSTALLATION_ID, "beta"),
    ]
    recorded = stored[1]
    assert recorded.account_type == "Organization"
    # In a single-tenant deployment the delivery lands in the one workspace
    assert recorded.workspace_id == seeded.workspace_id


async def test_an_installation_is_not_placed_without_a_workspace_to_place_it_in(
    solo_user_id: Any, build_harness: Any, session_factory: Any, webhook_secret: str
) -> None:
    # Given a deployment with no workspace at all (nobody has onboarded yet)
    harness: ApiHarness = await build_harness(user_id=solo_user_id)

    # When a delivery introduces an installation
    response = await deliver(harness, "installation", installation_payload("created"))

    # Then it is accepted and ignored: this endpoint has no session to take a
    # workspace from, so it must not guess one (v1 is single-tenant, and a
    # delivery that cannot be attributed is GitHub's to redeliver, not ours to
    # mis-file)
    assert response.status_code == 202
    assert response.json()["applied"] is False
    assert await row_counts(session_factory) == (0, 0)


async def test_a_deleted_installation_keeps_its_rows_and_retires_them(
    seeded: Any, build_harness: Any, session_factory: Any, webhook_secret: str
) -> None:
    # Given a recorded installation
    harness: ApiHarness = await build_harness(user_id=seeded.user_id)

    # When GitHub says the App was uninstalled
    response = await deliver(harness, "installation", installation_payload("deleted"))

    # Then no token can be minted for it any more, so its repositories are marked
    # unusable — but kept, because sessions and findings point at them
    assert response.status_code == 202
    stored = await installations(session_factory)
    assert [row.installation_id for row in stored] == [SEEDED_INSTALLATION_ID]
    assert [row.connected for row in await repositories(session_factory)] == [False]


async def test_a_suspended_installation_comes_back_on_unsuspend(
    seeded: Any, build_harness: Any, session_factory: Any, webhook_secret: str
) -> None:
    # Given a recorded installation
    harness: ApiHarness = await build_harness(user_id=seeded.user_id)

    # When GitHub suspends it
    assert (
        await deliver(harness, "installation", installation_payload("suspend"))
    ).status_code == 202
    assert [row.connected for row in await repositories(session_factory)] == [False]

    # And later lifts the suspension
    response = await deliver(harness, "installation", installation_payload("unsuspend"))

    # Then the workspace can review through it again without revisiting the
    # install page
    assert response.status_code == 202
    stored = await repositories(session_factory)
    assert [row.connected for row in stored] == [True]
    assert stored[0].full_name == SEEDED_FULL_NAME


# --- installation_repositories ----------------------------------------------


async def test_added_repositories_are_recorded_and_removed_ones_retired(
    seeded: Any, build_harness: Any, session_factory: Any, webhook_secret: str
) -> None:
    # Given an installation whose repository selection changes
    harness: ApiHarness = await build_harness(user_id=seeded.user_id)
    payload = {
        "action": "added",
        "installation": {"id": SEEDED_INSTALLATION_ID},
        "repositories_added": [
            {
                "id": NEW_REPOSITORY_ID,
                "full_name": "acme/service",
                "private": True,
                "default_branch": "develop",
            }
        ],
        "repositories_removed": [
            {
                "id": SEEDED_REPOSITORY_ID,
                "full_name": SEEDED_FULL_NAME,
                "private": False,
                "default_branch": "main",
            }
        ],
    }

    # When the delivery arrives twice (GitHub retries on any non-2xx)
    first = await deliver(harness, "installation_repositories", payload)
    second = await deliver(harness, "installation_repositories", payload)

    # Then both are accepted, the added repository is usable, and the removed one
    # is kept as history but refused
    assert first.status_code == 202
    assert first.json() == {"event": "installation_repositories", "applied": True}
    assert second.status_code == 202
    by_name = {row.full_name: row for row in await repositories(session_factory)}
    assert set(by_name) == {SEEDED_FULL_NAME, "acme/service"}
    assert by_name["acme/service"].private is True
    assert by_name["acme/service"].default_branch == "develop"
    assert by_name["acme/service"].connected is True
    assert by_name[SEEDED_FULL_NAME].connected is False


async def test_a_delivery_for_an_unknown_installation_is_ignored(
    seeded: Any, build_harness: Any, session_factory: Any, webhook_secret: str
) -> None:
    # Given deliveries naming an installation this workspace has never recorded
    harness: ApiHarness = await build_harness(user_id=seeded.user_id)
    added = {
        "action": "added",
        "installation": {"id": UNKNOWN_INSTALLATION_ID},
        "repositories_added": [
            {
                "id": NEW_REPOSITORY_ID,
                "full_name": "stranger/service",
                "private": False,
                "default_branch": "main",
            }
        ],
        "repositories_removed": [],
    }
    renamed = {
        "action": "renamed",
        "installation": {"id": UNKNOWN_INSTALLATION_ID},
        "repository": {
            "id": 8181,
            "full_name": "stranger/renamed",
            "private": False,
            "default_branch": "main",
        },
    }

    # When they arrive
    unknown_delete = installation_payload(
        "deleted", installation_id=UNKNOWN_INSTALLATION_ID
    )
    responses = [
        await deliver(harness, "installation_repositories", added),
        await deliver(harness, "repository", renamed),
        await deliver(harness, "installation", unknown_delete),
    ]

    # Then nothing is written and nothing is reported as applied — an unknown
    # installation is never a 500
    assert [response.status_code for response in responses] == [202, 202, 202]
    assert [response.json()["applied"] for response in responses] == [False, False, False]
    assert await row_counts(session_factory) == (1, 1)
    assert (await repositories(session_factory))[0].full_name == SEEDED_FULL_NAME


# --- repository -------------------------------------------------------------


async def test_a_renamed_repository_keeps_one_row_and_its_new_name(
    seeded: Any, build_harness: Any, session_factory: Any, webhook_secret: str
) -> None:
    # Given a recorded repository
    harness: ApiHarness = await build_harness(user_id=seeded.user_id)

    # When GitHub reports it renamed, then transferred to another owner
    renamed = await deliver(
        harness,
        "repository",
        repository_payload(
            "renamed",
            full_name="acme/api-service",
            changes={"repository": {"name": {"from": "api"}}},
        ),
    )
    transferred = await deliver(
        harness,
        "repository",
        repository_payload(
            "transferred",
            full_name="beta/api-service",
            changes={"owner": {"from": {"login": "acme", "type": "Organization"}}},
        ),
    )

    # Then the numeric id still names one row, under its new name
    assert renamed.status_code == 202
    assert renamed.json()["applied"] is True
    assert transferred.status_code == 202
    stored = await repositories(session_factory)
    assert len(stored) == 1
    assert stored[0].github_id == SEEDED_REPOSITORY_ID
    assert stored[0].full_name == "beta/api-service"
    assert stored[0].private is False


async def test_a_rename_is_followed_for_a_row_that_has_no_github_id_yet(
    seeded: Any, build_harness: Any, session_factory: Any, webhook_secret: str
) -> None:
    # Given the workspace's row carries no GitHub id yet — the shape a repository
    # created from a bare PR link has
    async with session_factory() as session:
        stored = await session.scalar(
            select(Repository).where(Repository.full_name == SEEDED_FULL_NAME)
        )
        assert stored is not None
        stored.github_id = 0
        await session.commit()

    harness: ApiHarness = await build_harness(user_id=seeded.user_id)

    # When a rename arrives, matching the row only by the name it had
    response = await deliver(
        harness,
        "repository",
        repository_payload(
            "renamed",
            full_name="acme/api-service",
            changes={"repository": {"name": {"from": "api"}}},
        ),
    )

    # Then the row is followed rather than duplicated, and comes away with the
    # real id every later delivery is matched on
    assert response.status_code == 202
    stored_rows = await repositories(session_factory)
    assert [row.full_name for row in stored_rows] == ["acme/api-service"]
    assert stored_rows[0].github_id == SEEDED_REPOSITORY_ID


async def test_a_repository_the_workspace_has_never_recorded_is_added(
    seeded: Any, build_harness: Any, session_factory: Any, webhook_secret: str
) -> None:
    # Given GitHub reports a repository in a known installation that the
    # workspace has no row for
    harness: ApiHarness = await build_harness(user_id=seeded.user_id)

    # When the delivery arrives
    response = await deliver(
        harness,
        "repository",
        repository_payload("renamed", id=NEW_REPOSITORY_ID, full_name="acme/service"),
    )

    # Then it is recorded as usable, because the App can read it
    assert response.status_code == 202
    assert response.json()["applied"] is True
    by_name = {row.full_name: row for row in await repositories(session_factory)}
    assert set(by_name) == {SEEDED_FULL_NAME, "acme/service"}
    assert by_name["acme/service"].github_id == NEW_REPOSITORY_ID
    assert by_name["acme/service"].connected is True


async def test_a_deleted_repository_is_retired_not_removed(
    seeded: Any, build_harness: Any, session_factory: Any, webhook_secret: str
) -> None:
    # Given a recorded repository
    harness: ApiHarness = await build_harness(user_id=seeded.user_id)

    # When GitHub reports it deleted
    response = await deliver(harness, "repository", repository_payload("deleted"))

    # Then it is kept for its history but no longer usable
    assert response.status_code == 202
    stored = await repositories(session_factory)
    assert [row.full_name for row in stored] == [SEEDED_FULL_NAME]
    assert stored[0].connected is False
