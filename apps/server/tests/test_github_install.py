"""GitHub App install + setup callback (spec 10.1).

The setup URL is the only place an installation id reaches the server, so these
tests cover what it records, what it refuses, and where it sends the browser when
the visitor cannot act yet.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.main import create_app
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from slopolis_core.github.models import AppInstallation, InstallationRepository
from slopolis_db.models import GitHubInstallation, Repository

from .conftest import ApiHarness, FakeAppInstallations

INSTALLATION_ID = 555
APP_URL = "http://localhost:8400"


def installation(
    *,
    installation_id: int = INSTALLATION_ID,
    login: str = "acme",
    account_type: str = "Organization",
    suspended: bool = False,
) -> AppInstallation:
    return AppInstallation(
        installation_id=installation_id,
        account_login=login,
        account_type=account_type,
        repository_selection="selected",
        suspended=suspended,
    )


def repository(
    full_name: str, *, github_id: int = 4242, private: bool = False
) -> InstallationRepository:
    return InstallationRepository(
        github_id=github_id,
        full_name=full_name,
        private=private,
        default_branch="main",
    )


async def repositories_in(session_factory: Any, workspace_id: uuid.UUID) -> list[Repository]:
    async with session_factory() as session:
        return list(
            (
                await session.scalars(
                    select(Repository)
                    .where(Repository.workspace_id == workspace_id)
                    .order_by(Repository.full_name)
                )
            ).all()
        )


async def test_install_redirects_to_the_apps_install_page(
    seeded: Any, build_harness: Any
) -> None:
    # Given a configured App whose slug the server has to ask GitHub for
    source = FakeAppInstallations(slug="slopolis-dev")
    harness: ApiHarness = await build_harness(
        user_id=seeded.user_id, app_installations=source
    )

    # When the install route is hit twice
    first = await harness.client.get("/api/github/install", follow_redirects=False)
    second = await harness.client.get("/api/github/install", follow_redirects=False)

    # Then both redirect to GitHub's install page
    assert first.status_code == 302
    assert first.headers["location"] == (
        "https://github.com/apps/slopolis-dev/installations/new"
    )
    assert second.headers["location"] == first.headers["location"]
    # And the slug is asked for once, then cached on the app
    assert [call for call in source.calls if call[0] == "app_slug"] == [("app_slug", None)]


async def test_install_reports_an_unknown_slug(seeded: Any, build_harness: Any) -> None:
    # Given GitHub returns no slug for the App
    source = FakeAppInstallations(slug="")
    harness: ApiHarness = await build_harness(
        user_id=seeded.user_id, app_installations=source
    )

    # When the install route is hit
    response = await harness.client.get("/api/github/install", follow_redirects=False)

    # Then the operator is told which setting to fix
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "github_app_slug_missing"


async def test_setup_records_the_installation_and_its_repositories(
    seeded: Any, build_harness: Any, session_factory: Any
) -> None:
    # Given an App installed on two repositories
    source = FakeAppInstallations(
        installations={INSTALLATION_ID: installation()},
        repositories={
            INSTALLATION_ID: [
                repository("acme/api", github_id=1),
                repository("acme/web", github_id=2, private=True),
            ]
        },
    )
    harness: ApiHarness = await build_harness(
        user_id=seeded.user_id, app_installations=source
    )

    # When GitHub sends the browser to the setup URL
    response = await harness.client.get(
        f"/api/github/setup?installation_id={INSTALLATION_ID}&setup_action=install",
        follow_redirects=False,
    )

    # Then the browser lands back in the app
    assert response.status_code == 302
    assert response.headers["location"] == APP_URL

    # And the installation plus its repositories belong to the workspace
    async with session_factory() as session:
        row = await session.scalar(
            select(GitHubInstallation).where(
                GitHubInstallation.installation_id == INSTALLATION_ID
            )
        )
    assert row is not None
    assert row.workspace_id == seeded.workspace_id
    assert row.account_login == "acme"
    assert row.account_type == "Organization"

    stored = await repositories_in(session_factory, seeded.workspace_id)
    by_name = {item.full_name: item for item in stored}
    assert set(by_name) == {"acme/api", "acme/web"}
    assert by_name["acme/web"].private is True
    assert all(item.connected for item in stored)
    assert by_name["acme/api"].installation_id == row.id


async def test_setup_is_idempotent(
    seeded: Any, build_harness: Any, session_factory: Any
) -> None:
    # Given an installation already synced once
    source = FakeAppInstallations(
        installations={INSTALLATION_ID: installation()},
        repositories={INSTALLATION_ID: [repository("acme/api", github_id=1)]},
    )
    harness: ApiHarness = await build_harness(
        user_id=seeded.user_id, app_installations=source
    )
    path = f"/api/github/setup?installation_id={INSTALLATION_ID}&setup_action=update"
    assert (await harness.client.get(path, follow_redirects=False)).status_code == 302

    # When the update redirect arrives again
    assert (await harness.client.get(path, follow_redirects=False)).status_code == 302

    # Then nothing is duplicated
    async with session_factory() as session:
        installations = list(
            (
                await session.scalars(
                    select(GitHubInstallation).where(
                        GitHubInstallation.installation_id == INSTALLATION_ID
                    )
                )
            ).all()
        )
    assert len(installations) == 1
    stored = await repositories_in(session_factory, seeded.workspace_id)
    assert [item.full_name for item in stored] == ["acme/api"]
    assert stored[0].installation_id == installations[0].id


async def test_setup_disconnects_repositories_that_left_the_installation(
    seeded: Any, build_harness: Any, session_factory: Any
) -> None:
    # Given an installation that first covered two repositories
    source = FakeAppInstallations(
        installations={INSTALLATION_ID: installation()},
        repositories={
            INSTALLATION_ID: [
                repository("acme/api", github_id=1),
                repository("acme/web", github_id=2),
            ]
        },
    )
    harness: ApiHarness = await build_harness(
        user_id=seeded.user_id, app_installations=source
    )
    path = f"/api/github/setup?installation_id={INSTALLATION_ID}&setup_action=install"
    await harness.client.get(path, follow_redirects=False)

    # When one is removed from the installation and GitHub redirects again
    source.repositories[INSTALLATION_ID] = [repository("acme/api", github_id=1)]
    await harness.client.get(path, follow_redirects=False)

    # Then the row is kept as history but marked unusable
    stored = {
        item.full_name: item
        for item in await repositories_in(session_factory, seeded.workspace_id)
    }
    assert stored["acme/api"].connected is True
    assert stored["acme/web"].connected is False


async def test_setup_refuses_an_installation_recorded_for_another_workspace(
    seeded: Any, build_harness: Any, session_factory: Any
) -> None:
    # Given the installation already belongs to a different workspace
    claimed_id = INSTALLATION_ID + 1
    async with session_factory() as session:
        session.add(
            GitHubInstallation(
                workspace_id=uuid.uuid4(),
                installation_id=claimed_id,
                account_login="someone-else",
                account_type="Organization",
            )
        )
        await session.commit()

    source = FakeAppInstallations(
        installations={claimed_id: installation(installation_id=claimed_id)},
        repositories={claimed_id: [repository("acme/api", github_id=1)]},
    )
    harness: ApiHarness = await build_harness(
        user_id=seeded.user_id, app_installations=source
    )

    # When the setup callback tries to record it for this workspace
    response = await harness.client.get(
        f"/api/github/setup?installation_id={claimed_id}", follow_redirects=False
    )

    # Then the conflict is reported instead of silently re-pointing the row
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "installation_claimed"


async def test_setup_reports_an_installation_github_does_not_know(
    seeded: Any, build_harness: Any
) -> None:
    # Given GitHub has no such installation
    source = FakeAppInstallations(missing={999})
    harness: ApiHarness = await build_harness(
        user_id=seeded.user_id, app_installations=source
    )

    # When the setup callback arrives for it
    response = await harness.client.get(
        "/api/github/setup?installation_id=999", follow_redirects=False
    )

    # Then the caller gets the standard not-found envelope
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "github_not_found"


async def test_setup_suspends_an_installation_and_disconnects_its_repositories(
    seeded: Any, build_harness: Any, session_factory: Any
) -> None:
    # Given an installation GitHub reports as suspended
    source = FakeAppInstallations(
        installations={INSTALLATION_ID: installation(suspended=True)},
        repositories={INSTALLATION_ID: [repository("acme/api", github_id=1)]},
    )
    harness: ApiHarness = await build_harness(
        user_id=seeded.user_id, app_installations=source
    )

    # When the setup callback records it
    response = await harness.client.get(
        f"/api/github/setup?installation_id={INSTALLATION_ID}", follow_redirects=False
    )

    # Then it is recorded without asking GitHub for a repository list (the token
    # could not be minted), and the repositories it already owns are marked
    # unusable rather than left looking connected
    assert response.status_code == 302
    assert ("list_repositories", INSTALLATION_ID) not in source.calls
    stored = await repositories_in(session_factory, seeded.workspace_id)
    assert [item.full_name for item in stored] == ["acme/api"]
    assert stored[0].connected is False


async def test_setup_sends_a_workspace_less_account_back_to_the_app(
    solo_user_id: Any, build_harness: Any, session_factory: Any
) -> None:
    # Given an account that has not onboarded yet
    source = FakeAppInstallations(
        installations={INSTALLATION_ID: installation()},
        repositories={INSTALLATION_ID: [repository("acme/api", github_id=1)]},
    )
    harness: ApiHarness = await build_harness(
        user_id=solo_user_id, app_installations=source
    )

    # When the setup callback arrives
    response = await harness.client.get(
        f"/api/github/setup?installation_id={INSTALLATION_ID}", follow_redirects=False
    )

    # Then the browser goes back to the app (which shows onboarding) and nothing
    # is written against a workspace that does not exist
    assert response.status_code == 302
    assert response.headers["location"] == APP_URL
    assert source.calls == []
    async with session_factory() as session:
        assert (
            await session.scalar(
                select(GitHubInstallation).where(
                    GitHubInstallation.installation_id == INSTALLATION_ID
                )
            )
        ) is None


async def test_setup_ignores_an_installation_request(
    seeded: Any, build_harness: Any
) -> None:
    # Given GitHub reports only an approval *request*, not an install
    source = FakeAppInstallations()
    harness: ApiHarness = await build_harness(
        user_id=seeded.user_id, app_installations=source
    )

    # When the setup callback arrives
    response = await harness.client.get(
        f"/api/github/setup?installation_id={INSTALLATION_ID}&setup_action=request",
        follow_redirects=False,
    )

    # Then nothing is recorded and GitHub is not called
    assert response.status_code == 302
    assert response.headers["location"] == APP_URL
    assert source.calls == []


async def test_setup_reports_an_unconfigured_app(seeded: Any, build_harness: Any) -> None:
    # Given a deployment with no App credentials wired
    harness: ApiHarness = await build_harness(user_id=seeded.user_id)

    # When the setup callback arrives
    response = await harness.client.get(
        f"/api/github/setup?installation_id={INSTALLATION_ID}", follow_redirects=False
    )

    # Then the operator gets the configuration error rather than a 500
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "github_not_configured"


async def test_setup_redirects_an_anonymous_browser_to_login() -> None:
    # Given a browser with no session cookie (no dependency overrides at all)
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        # When GitHub sends it to the setup URL
        response = await client.get(
            f"/api/github/setup?installation_id={INSTALLATION_ID}",
            follow_redirects=False,
        )

    # Then it is sent to sign in rather than shown a JSON 401
    assert response.status_code == 302
    assert response.headers["location"] == "/api/auth/github/login"
