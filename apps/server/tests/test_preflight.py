"""Pre-flight validation endpoint."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import httpx
from app.adapters.github import GitHubGatewayAdapter
from app.adapters.workspace import WorkspaceConfigAdapter
from app.main import create_app
from app.services.github_clients import WorkspaceRepositories
from sqlalchemy import func, select

from slopolis_core.github.errors import GitHubNotFoundError
from slopolis_core.github.models import GitHubPullRequest, InstallationRepository
from slopolis_core.llm.client import LiteLlmClient, LlmAuthError
from slopolis_db.models import Repository, ReviewSession

from .conftest import (
    ApiHarness,
    FakeGateway,
    FakeInstallationClients,
    FakeLiveCheck,
    FakeWorkspace,
    add_installation,
    make_ref,
    seed_empty_workspace,
    seed_review_model,
)

_VALID_URL = "https://github.com/acme/api/pull/11"
_UNKNOWN_URL = "https://github.com/other/repo/pull/3"
_WIDGETS_URL = "https://github.com/widgets/app/pull/5"


class GatewayClient:
    """Fake GitHub client with the surface pre-flight's gateway reads through.

    Records every call, so a test can assert which installation's client served
    a link, and which parts of the port each one was asked for.
    """

    def __init__(
        self,
        *,
        installation_id: int,
        covered: list[str],
        pulls: dict[str, GitHubPullRequest] | None = None,
        files: dict[tuple[str, str], str] | None = None,
        access: bool = True,
    ) -> None:
        self.installation_id = installation_id
        self.covered = covered
        self.pulls = pulls or {}
        self.files = files or {}
        self.access = access
        self.calls: list[str] = []
        #: The access override each check was asked for (spec 10.10).
        self.required_levels: list[str | None] = []

    async def list_installation_repositories(self) -> list[InstallationRepository]:
        self.calls.append("list_installation_repositories")
        return [
            InstallationRepository(
                github_id=index,
                full_name=full_name,
                private=False,
                default_branch="main",
            )
            for index, full_name in enumerate(self.covered, start=1)
        ]

    async def resolve_pr(self, url: str) -> GitHubPullRequest:
        self.calls.append(f"resolve_pr:{url}")
        pull = self.pulls.get(url)
        if pull is None:
            raise GitHubNotFoundError(f"No pull request at {url}")
        return pull

    async def user_can_trigger(
        self,
        repo_full_name: str,
        *,
        private: bool,
        user_login: str,
        required: str | None = None,
    ) -> bool:
        self.calls.append(f"user_can_trigger:{repo_full_name}")
        self.required_levels.append(required)
        return self.access

    async def list_open_pull_requests(self, full_name: str) -> list[GitHubPullRequest]:
        """A repository switch reports the open count; this fake has none."""
        self.calls.append(f"list_open_pull_requests:{full_name}")
        return []

    async def read_file(self, repo_full_name: str, path: str, ref: str) -> str:
        self.calls.append(f"read_file:{repo_full_name}:{path}")
        text = self.files.get((repo_full_name, path))
        if text is None:
            raise GitHubNotFoundError(f"{path} is not in {repo_full_name}")
        return text


def _pull(repo: str, number: int) -> GitHubPullRequest:
    """Build a typed GitHub pull request for the fake client."""
    return GitHubPullRequest(
        repo_full_name=repo,
        private=False,
        number=number,
        title=f"fix: {repo} change {number}",
        url=f"https://github.com/{repo}/pull/{number}",
        head_branch="fix/branch",
        base_branch="main",
        head_sha="abc123",
        default_branch="main",
        body="",
        author_login="octocat",
        draft=False,
        changed_files=3,
        additions=40,
        deletions=7,
        updated_at="2026-01-01T00:00:00Z",
    )


async def test_preflight_splits_valid_and_invalid(seeded: Any, build_harness: Any) -> None:
    # Given a gateway that resolves one link and rejects the other
    gateway = FakeGateway(refs={_VALID_URL: make_ref("acme/api", 11)})
    harness: ApiHarness = await build_harness(user_id=seeded.user_id, gateway=gateway)

    # When pre-flight runs over both links
    response = await harness.client.post(
        "/api/reviews/preflight", json={"prUrls": [_VALID_URL, _UNKNOWN_URL]}
    )

    # Then the outcome splits them and records a notice
    assert response.status_code == 200
    body = response.json()
    assert [item["url"] for item in body["valid"]] == [_VALID_URL]
    assert body["valid"][0]["repository"]["fullName"] == "acme/api"
    assert body["invalid"] == [_UNKNOWN_URL]
    assert body["notices"]


async def test_preflight_reports_uncovered_repository(seeded: Any, build_harness: Any) -> None:
    # Given a PR in a repo the installation does not cover
    gateway = FakeGateway(refs={_VALID_URL: make_ref("acme/private")}, covered=["acme/api"])
    harness: ApiHarness = await build_harness(user_id=seeded.user_id, gateway=gateway)

    # When pre-flight runs
    response = await harness.client.post("/api/reviews/preflight", json={"prUrls": [_VALID_URL]})

    # Then the link is invalid with an explanatory notice
    body = response.json()
    assert body["valid"] == []
    assert body["invalid"] == [_VALID_URL]
    assert any("not covered" in notice for notice in body["notices"])


async def test_preflight_fails_without_model(seeded: Any, build_harness: Any) -> None:
    # Given a workspace with no assigned model
    harness: ApiHarness = await build_harness(
        user_id=seeded.user_id, workspace=FakeWorkspace(model=None)
    )

    # When pre-flight runs
    response = await harness.client.post("/api/reviews/preflight", json={"prUrls": [_VALID_URL]})

    # Then no target validates and the missing model is surfaced
    body = response.json()
    assert body["valid"] == []
    assert any("No review model" in notice for notice in body["notices"])


async def test_preflight_fails_when_live_check_fails(seeded: Any, build_harness: Any) -> None:
    # Given a gateway that resolves the link but a failing live model check
    gateway = FakeGateway(refs={_VALID_URL: make_ref("acme/api", 11)})
    harness: ApiHarness = await build_harness(
        user_id=seeded.user_id,
        gateway=gateway,
        live_check=FakeLiveCheck(fail=True),
    )

    # When pre-flight runs
    response = await harness.client.post("/api/reviews/preflight", json={"prUrls": [_VALID_URL]})

    # Then the link is rejected and no session is created
    body = response.json()
    assert body["valid"] == []
    assert any("Live model check failed" in notice for notice in body["notices"])


async def test_preflight_never_creates_a_session(
    seeded: Any, session_factory: Any, build_harness: Any
) -> None:
    # Given a successful pre-flight run
    gateway = FakeGateway(refs={_VALID_URL: make_ref("acme/api", 11)})
    harness: ApiHarness = await build_harness(user_id=seeded.user_id, gateway=gateway)
    await harness.client.post("/api/reviews/preflight", json={"prUrls": [_VALID_URL]})

    # Then the session table is unchanged
    async with session_factory() as session:
        count = await session.scalar(select(func.count(ReviewSession.id)))
    assert count == 1


async def test_preflight_reads_a_link_through_its_repositorys_installation(
    seeded: Any, session_factory: Any, build_harness: Any
) -> None:
    # Given a workspace holding two installations, each with a repository
    async with session_factory() as session:
        await add_installation(
            session,
            seeded.workspace_id,
            installation_id=777,
            account_login="widgets",
            repositories=["widgets/app"],
        )
    async with session_factory() as session:
        await seed_review_model(session, seeded.workspace_id)
    acme = GatewayClient(installation_id=555, covered=["acme/api"])
    widgets = GatewayClient(
        installation_id=777,
        covered=["widgets/app"],
        pulls={_WIDGETS_URL: _pull("widgets/app", 5)},
    )
    harness: ApiHarness = await build_harness(
        user_id=seeded.user_id,
        real_preflight=True,
        github_clients=FakeInstallationClients({555: acme, 777: widgets}),
    )

    # When a link to the second installation's repository is pre-flighted
    response = await harness.client.post("/api/reviews/preflight", json={"prUrls": [_WIDGETS_URL]})

    # Then the link is valid, read entirely through its own installation's client
    assert response.status_code == 200
    body = response.json()
    assert [item["url"] for item in body["valid"]] == [_WIDGETS_URL]
    assert body["valid"][0]["repository"]["fullName"] == "widgets/app"
    assert widgets.calls == [
        "list_installation_repositories",
        f"resolve_pr:{_WIDGETS_URL}",
        "user_can_trigger:widgets/app",
        "read_file:widgets/app:.codereview.yml",
    ]
    # ...while the other installation was only asked what it covers
    assert acme.calls == ["list_installation_repositories"]


async def test_preflight_fails_like_a_missing_client_without_an_installation(
    session_factory: Any, build_harness: Any
) -> None:
    # Given a workspace with no installation at all and no App registry
    async with session_factory() as session:
        workspace, user = await seed_empty_workspace(session)
    async with session_factory() as session:
        await seed_review_model(session, workspace.id)
    harness: ApiHarness = await build_harness(user_id=user.id, real_preflight=True)

    # When a link is pre-flighted
    response = await harness.client.post("/api/reviews/preflight", json={"prUrls": [_WIDGETS_URL]})

    # Then it fails exactly the way a workspace with no GitHub client always did
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "github_not_configured"


async def test_preflight_fails_like_a_missing_client_for_an_unmintable_installation(
    seeded: Any, session_factory: Any, build_harness: Any
) -> None:
    # Given a second installation the credentials cannot mint a token for
    async with session_factory() as session:
        await add_installation(
            session,
            seeded.workspace_id,
            installation_id=777,
            account_login="widgets",
            repositories=["widgets/app"],
        )
    async with session_factory() as session:
        await seed_review_model(session, seeded.workspace_id)
    acme = GatewayClient(
        installation_id=555,
        covered=["acme/api"],
        pulls={_VALID_URL: _pull("acme/api", 11)},
    )
    harness: ApiHarness = await build_harness(
        user_id=seeded.user_id,
        real_preflight=True,
        github_clients=FakeInstallationClients({555: acme}),
    )

    # Then a link on the readable installation still validates...
    covered = await harness.client.post("/api/reviews/preflight", json={"prUrls": [_VALID_URL]})
    assert covered.status_code == 200
    assert [item["url"] for item in covered.json()["valid"]] == [_VALID_URL]

    # ...while a link on the unreadable one is the missing-client failure, not a
    # silently "uncovered" link
    unreadable = await harness.client.post(
        "/api/reviews/preflight", json={"prUrls": [_WIDGETS_URL]}
    )
    assert unreadable.status_code == 503
    assert unreadable.json()["error"]["code"] == "github_not_configured"


async def test_preflight_refuses_a_parked_repository_by_its_reason(
    seeded: Any, session_factory: Any, build_harness: Any
) -> None:
    # Given a repository the workspace has parked
    async with session_factory() as session:
        await seed_review_model(session, seeded.workspace_id)
        row = await session.get(Repository, seeded.repository_id)
        assert row is not None
        row.enabled = False
        await session.commit()
    acme = GatewayClient(
        installation_id=555,
        covered=["acme/api"],
        pulls={_VALID_URL: _pull("acme/api", 11)},
    )
    harness: ApiHarness = await build_harness(
        user_id=seeded.user_id,
        real_preflight=True,
        github_clients=FakeInstallationClients({555: acme}),
    )

    # When a link in it is pre-flighted
    response = await harness.client.post("/api/reviews/preflight", json={"prUrls": [_VALID_URL]})

    # Then it is refused for being disabled, with the way back named — never as
    # an uncovered repository
    body = response.json()
    assert body["valid"] == []
    assert body["invalid"] == [_VALID_URL]
    assert any("acme/api is disabled in slopolis" in notice for notice in body["notices"])
    assert not any("not covered" in notice for notice in body["notices"])
    # ...and the link was refused right after resolution, before anything else
    # was read for it
    assert acme.calls == [
        "list_installation_repositories",
        f"resolve_pr:{_VALID_URL}",
    ]

    # When the workspace re-enables it
    reenabled = await harness.client.patch(
        f"/api/repositories/{seeded.repository_id}", json={"enabled": True}
    )
    assert reenabled.json()["enabled"] is True
    accepted = await harness.client.post("/api/reviews/preflight", json={"prUrls": [_VALID_URL]})

    # Then the very same link validates through its repository's installation
    assert [item["url"] for item in accepted.json()["valid"]] == [_VALID_URL]
    assert accepted.json()["notices"] == []


async def test_preflight_still_reports_a_repository_github_no_longer_grants(
    seeded: Any, session_factory: Any, build_harness: Any
) -> None:
    # Given a repository row the installation no longer lists
    async with session_factory() as session:
        await seed_review_model(session, seeded.workspace_id)
    acme = GatewayClient(
        installation_id=555,
        covered=["acme/other"],
        pulls={_VALID_URL: _pull("acme/api", 11)},
    )
    harness: ApiHarness = await build_harness(
        user_id=seeded.user_id,
        real_preflight=True,
        github_clients=FakeInstallationClients({555: acme}),
    )

    # When a link in it is pre-flighted
    response = await harness.client.post("/api/reviews/preflight", json={"prUrls": [_VALID_URL]})

    # Then it keeps the uncovered notice: a repository that is merely no longer
    # granted is not the same refusal as one the workspace parked
    body = response.json()
    assert body["valid"] == []
    assert any("acme/api is not covered" in notice for notice in body["notices"])
    assert not any("disabled" in notice for notice in body["notices"])


# --- the per-repository access override (spec 10.10) ------------------------


async def test_preflight_hands_the_resolved_override_to_the_access_check(
    seeded: Any, build_harness: Any
) -> None:
    # Given a workspace whose repository is held to write access
    workspace = FakeWorkspace(access="write")
    gateway = FakeGateway(refs={_VALID_URL: make_ref("acme/api", 11)})
    harness: ApiHarness = await build_harness(
        user_id=seeded.user_id, gateway=gateway, workspace=workspace
    )

    # When a link in it is pre-flighted
    response = await harness.client.post("/api/reviews/preflight", json={"prUrls": [_VALID_URL]})

    # Then the override was read for that repository and handed to the check,
    # which is the only thing that can apply it
    assert response.status_code == 200
    assert [item["url"] for item in response.json()["valid"]] == [_VALID_URL]
    assert workspace.access_calls == ["acme/api"]
    assert gateway.access_calls == [("acme/api", "write")]


async def test_the_workspace_adapter_reports_the_access_override(
    seeded: Any, session_factory: Any
) -> None:
    # Given a workspace whose one repository is stored on the spec rule
    async with session_factory() as session:
        row = await session.get(Repository, seeded.repository_id)
        assert row is not None
        assert row.required_access == "default"
        adapter = WorkspaceConfigAdapter(session, seeded.workspace_id)
        untouched = await adapter.required_access("acme/api")

        # When it is loosened and then put back on the default
        row.required_access = "read"
        await session.flush()
        loosened = await adapter.required_access("acme/api")
        row.required_access = "default"
        await session.flush()
        restored = await adapter.required_access("acme/api")
        unknown = await adapter.required_access("other/repo")

    # Then only a stored override is reported; the default rule and a
    # repository the workspace holds no row for both mean "apply the spec rule"
    assert untouched is None
    assert loosened == "read"
    assert restored is None
    assert unknown is None


async def test_the_gateway_adapter_passes_the_override_to_github(
    seeded: Any, session_factory: Any
) -> None:
    # Given a repository read through its own installation's client
    client = GatewayClient(installation_id=555, covered=["acme/api"])
    registry: FakeInstallationClients[Any] = FakeInstallationClients({555: client})

    # When the gateway checks access with a resolved override
    async with session_factory() as session:
        gateway = GitHubGatewayAdapter(
            WorkspaceRepositories(session, seeded.workspace_id, registry)
        )
        allowed = await gateway.user_has_access(
            "acme/api", private=True, user_login="octocat", required="write"
        )

    # Then the level reaches the client, which is the thing that applies it
    assert allowed is True
    assert client.required_levels == ["write"]


async def test_preflight_applies_each_repositorys_access_override(
    seeded: Any, session_factory: Any, build_harness: Any
) -> None:
    # Given one repository tightened to write access and one left on the spec rule
    async with session_factory() as session:
        await seed_review_model(session, seeded.workspace_id)
        row = await session.get(Repository, seeded.repository_id)
        assert row is not None
        row.required_access = "write"
        await session.commit()
    async with session_factory() as session:
        await add_installation(
            session,
            seeded.workspace_id,
            installation_id=777,
            account_login="widgets",
            repositories=["widgets/app"],
        )
    acme = GatewayClient(
        installation_id=555,
        covered=["acme/api"],
        pulls={_VALID_URL: _pull("acme/api", 11)},
    )
    widgets = GatewayClient(
        installation_id=777,
        covered=["widgets/app"],
        pulls={_WIDGETS_URL: _pull("widgets/app", 5)},
    )
    harness: ApiHarness = await build_harness(
        user_id=seeded.user_id,
        real_preflight=True,
        github_clients=FakeInstallationClients({555: acme, 777: widgets}),
    )

    # When both links are pre-flighted
    response = await harness.client.post(
        "/api/reviews/preflight", json={"prUrls": [_VALID_URL, _WIDGETS_URL]}
    )

    # Then the overridden repository's check is told what it requires...
    assert response.status_code == 200
    assert [item["url"] for item in response.json()["valid"]] == [
        _VALID_URL,
        _WIDGETS_URL,
    ]
    assert acme.required_levels == ["write"]
    # ...while one on the spec rule is told nothing, i.e. the client decides
    assert widgets.required_levels == [None]


async def test_an_unconfigured_gateway_is_a_notice_not_a_failed_request(
    seeded: Any, session_factory: Any, build_harness: Any
) -> None:
    # Given a deployment with no model gateway, and a link that otherwise passes
    # (model assigned, credential ready, repository covered, access granted)
    async with session_factory() as session:
        await seed_review_model(session, seeded.workspace_id)
    acme = GatewayClient(
        installation_id=555,
        covered=["acme/api"],
        pulls={_VALID_URL: _pull("acme/api", 11)},
    )
    harness: ApiHarness = await build_harness(
        user_id=seeded.user_id,
        real_preflight=True,
        unconfigured_gateway=True,
        github_clients=FakeInstallationClients({555: acme}),
    )

    # When pre-flight runs through the real dependency assembly
    response = await harness.client.post("/api/reviews/preflight", json={"prUrls": [_VALID_URL]})

    # Then it answers the outcome rather than a 503: the missing gateway is one
    # more thing to fix, and the user can see it without losing the rest
    assert response.status_code == 200
    body = response.json()
    assert body["valid"] == []
    assert body["invalid"] == [_VALID_URL]
    assert any("LITELLM_MASTER_KEY" in notice for notice in body["notices"])


async def test_the_gateway_client_is_built_from_settings_at_boot() -> None:
    # An app that never opens a client cannot run a live check on any
    # deployment, however well its environment is configured. Driven through the
    # real lifespan, so the wiring itself is what is under test.
    app = create_app()
    with patch.object(LiteLlmClient, "from_settings", return_value=object()) as from_settings:
        async with app.router.lifespan_context(app):
            assert app.state.llm_client is not None

    from_settings.assert_called_once()


async def test_a_gateway_less_deployment_still_boots_and_serves() -> None:
    # Without LITELLM_MASTER_KEY the server must still start and answer: reads,
    # the install flow and settings do not need a model, and pre-flight is where
    # the missing gateway is explained.
    app = create_app()
    with patch.object(
        LiteLlmClient,
        "from_settings",
        side_effect=LlmAuthError("LITELLM_MASTER_KEY is not configured"),
    ):
        async with app.router.lifespan_context(app):
            assert app.state.llm_client is None
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://testserver"
            ) as client:
                assert (await client.get("/healthz")).status_code == 200
