"""Workspace onboarding: identity without a workspace, and creating one.

Signing in creates an account; belonging to a workspace is a separate step, so a
fresh account must be able to see that state, create a workspace, and be refused
workspace-scoped work until then.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select

from slopolis_db.models import AuditLog, User, Workspace

from .conftest import ApiHarness, seed_solo_user, seed_workspace


async def test_me_reports_no_workspace_for_a_fresh_account(
    solo_user_id: Any, build_harness: Any
) -> None:
    # Given an account that signed in but belongs nowhere
    harness: ApiHarness = await build_harness(user_id=solo_user_id)

    # When identity is requested
    response = await harness.client.get("/api/me")

    # Then the wire reference says so explicitly
    assert response.status_code == 200
    body = response.json()
    assert body["handle"] == "newcomer"
    assert body["workspace"] is None
    assert body["isAdmin"] is False


async def test_me_reports_the_workspace_for_a_member(
    seeded: Any, build_harness: Any
) -> None:
    # Given a user who belongs to a workspace
    harness: ApiHarness = await build_harness(user_id=seeded.user_id)

    # When identity is requested
    response = await harness.client.get("/api/me")

    # Then the workspace travels with it, so the shell can name it
    assert response.status_code == 200
    workspace = response.json()["workspace"]
    assert workspace["id"] == str(seeded.workspace_id)
    assert workspace["name"] == "Acme"
    assert workspace["slug"] == "acme"


async def test_creating_a_workspace_attaches_the_caller_as_admin(
    solo_user_id: Any, build_harness: Any, session_factory: Any
) -> None:
    # Given a fresh account and nothing else
    harness: ApiHarness = await build_harness(user_id=solo_user_id)

    # When they create a workspace
    response = await harness.client.post("/api/workspaces", json={"name": "  Acme API  "})

    # Then it is created, named as typed, with a derived slug
    assert response.status_code == 201, response.text
    created = response.json()
    assert created["name"] == "Acme API"
    assert created["slug"] == "acme-api"

    # And the caller now belongs to it as an admin
    me = await harness.client.get("/api/me")
    assert me.json()["workspace"] == created
    assert me.json()["isAdmin"] is True
    listed = await harness.client.get("/api/workspaces")
    assert listed.json()["items"] == [created]

    async with session_factory() as session:
        user = await session.get(User, solo_user_id)
        assert user is not None
        assert user.workspace_id is not None
        workspace = await session.get(Workspace, user.workspace_id)
        assert workspace is not None and workspace.slug == "acme-api"


async def test_a_second_workspace_is_refused(
    solo_user_id: Any, build_harness: Any
) -> None:
    # Given someone who already created one
    harness: ApiHarness = await build_harness(user_id=solo_user_id)
    assert (await harness.client.post("/api/workspaces", json={"name": "Acme"})).status_code == 201

    # When they try to create another
    response = await harness.client.post("/api/workspaces", json={"name": "Other"})

    # Then v1 keeps one workspace per account and says so
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "already_in_workspace"


async def test_a_blank_name_is_rejected(solo_user_id: Any, build_harness: Any) -> None:
    harness: ApiHarness = await build_harness(user_id=solo_user_id)

    response = await harness.client.post("/api/workspaces", json={"name": "   "})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "name_required"


async def test_slugs_stay_unique_across_accounts(
    build_harness: Any, session_factory: Any
) -> None:
    # Given two accounts that pick the same workspace name
    async with session_factory() as session:
        first = await seed_solo_user(session, github_id=3001, handle="first")
        second = await seed_solo_user(session, github_id=3002, handle="second")

    first_harness: ApiHarness = await build_harness(user_id=first.id)
    second_harness: ApiHarness = await build_harness(user_id=second.id)

    # When both create it
    first_created = await first_harness.client.post("/api/workspaces", json={"name": "Acme API"})
    second_created = await second_harness.client.post("/api/workspaces", json={"name": "Acme API"})

    # Then the slugs do not collide
    assert first_created.json()["slug"] == "acme-api"
    assert second_created.json()["slug"] == "acme-api-2"


async def test_workspace_scoped_routes_refuse_an_account_without_one(
    solo_user_id: Any, build_harness: Any
) -> None:
    # Given a fresh account
    harness: ApiHarness = await build_harness(user_id=solo_user_id)

    # When they ask for workspace data
    for path in (
        "/api/repositories",
        "/api/pull-requests",
        "/api/sessions",
        "/api/models",
        "/api/workspaces/settings",
    ):
        response = await harness.client.get(path)

        # Then every one of them points at the missing workspace instead of
        # returning empty data that looks like an empty account
        assert response.status_code == 409, path
        assert response.json()["error"]["code"] == "no_workspace", path


# --- settings (spec 10.10): caps are opt-in, admin-only, and audited ---------


async def seed_admin(session_factory: Any) -> uuid.UUID:
    """Seed one workspace whose member is its admin; return the user id."""
    async with session_factory() as session:
        _workspace, user, _repository = await seed_workspace(session)
        user.is_admin = True
        await session.commit()
        return user.id


async def test_settings_default_to_unlimited(
    session_factory: Any, build_harness: Any
) -> None:
    # Given an admin of a workspace that never set a cap
    harness: ApiHarness = await build_harness(user_id=await seed_admin(session_factory))

    # When the settings are read
    response = await harness.client.get("/api/workspaces/settings")

    # Then every cap is null, which is what "opt-in" means on the wire
    assert response.status_code == 200
    assert response.json() == {
        "maxConcurrentSessions": None,
        "maxSessionsPerUserPerDay": None,
        "maxTargetsPerRepo": None,
        "maxTargetsPerInstallation": None,
    }


async def test_a_partial_settings_patch_audits_only_what_changed(
    session_factory: Any, build_harness: Any
) -> None:
    # Given an admin of a workspace with no caps
    user_id = await seed_admin(session_factory)
    harness: ApiHarness = await build_harness(user_id=user_id)

    # When two caps are set and later one of them is cleared
    patched = await harness.client.patch(
        "/api/workspaces/settings",
        json={"maxConcurrentSessions": 3, "maxTargetsPerRepo": 2},
    )
    cleared = await harness.client.patch(
        "/api/workspaces/settings", json={"maxConcurrentSessions": None}
    )

    # Then the response carries the whole settings shape at each step, with
    # omitted caps left exactly as they were
    assert patched.status_code == 200
    assert patched.json() == {
        "maxConcurrentSessions": 3,
        "maxSessionsPerUserPerDay": None,
        "maxTargetsPerRepo": 2,
        "maxTargetsPerInstallation": None,
    }
    assert cleared.json() == {
        "maxConcurrentSessions": None,
        "maxSessionsPerUserPerDay": None,
        "maxTargetsPerRepo": 2,
        "maxTargetsPerInstallation": None,
    }

    # And each change is recorded against the actor, naming only what it changed
    async with session_factory() as session:
        audits = list((await session.scalars(select(AuditLog))).all())
    assert [(audit.action, audit.actor_user_id) for audit in audits] == [
        ("settings.updated", user_id),
        ("settings.updated", user_id),
    ]
    assert audits[0].detail == {"maxConcurrentSessions": 3, "maxTargetsPerRepo": 2}
    assert audits[1].detail == {"maxConcurrentSessions": None}


async def test_a_settings_patch_that_changes_nothing_is_not_audited(
    session_factory: Any, build_harness: Any
) -> None:
    # Given an admin who already set a cap
    user_id = await seed_admin(session_factory)
    harness: ApiHarness = await build_harness(user_id=user_id)
    await harness.client.patch("/api/workspaces/settings", json={"maxConcurrentSessions": 3})

    # When the same value is sent again
    response = await harness.client.patch(
        "/api/workspaces/settings", json={"maxConcurrentSessions": 3}
    )

    # Then the answer is unchanged and no second audit row was written
    assert response.status_code == 200
    assert response.json()["maxConcurrentSessions"] == 3
    async with session_factory() as session:
        actions = [
            audit.action for audit in (await session.scalars(select(AuditLog))).all()
        ]
    assert actions == ["settings.updated"]


async def test_settings_caps_must_be_positive_integers(
    session_factory: Any, build_harness: Any
) -> None:
    # Given an admin
    harness: ApiHarness = await build_harness(user_id=await seed_admin(session_factory))

    # When a cap below one is sent
    for body in ({"maxConcurrentSessions": 0}, {"maxSessionsPerUserPerDay": -1}):
        response = await harness.client.patch("/api/workspaces/settings", json=body)

        # Then it is a validation failure, not a stored cap
        assert response.status_code == 422, body
        assert response.json()["error"]["code"] == "validation_error", body

    # And an unknown key is refused rather than silently ignored
    unknown = await harness.client.patch("/api/workspaces/settings", json={"maxSessions": 1})
    assert unknown.status_code == 422


async def test_settings_are_admin_only(
    seeded: Any, session_factory: Any, build_harness: Any
) -> None:
    # Given a signed-in workspace member who is not an admin
    harness: ApiHarness = await build_harness(user_id=seeded.user_id)

    # When they read or write the settings
    read = await harness.client.get("/api/workspaces/settings")
    write = await harness.client.patch(
        "/api/workspaces/settings", json={"maxConcurrentSessions": 1}
    )

    # Then both are refused with the admin code, and nothing is written
    assert read.status_code == 403
    assert read.json()["error"]["code"] == "admin_required"
    assert write.status_code == 403
    assert write.json()["error"]["code"] == "admin_required"
    async with session_factory() as session:
        assert await session.scalar(select(AuditLog).limit(1)) is None
