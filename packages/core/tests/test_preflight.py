"""Tests for synchronous pre-flight validation (spec 10.3).

Uses in-memory fakes for the gateway, workspace, and live check ports.
"""

import pytest

from slopolis_core.config.repo_config import RepoConfigError, parse_repo_config
from slopolis_core.llm.client import LlmError
from slopolis_core.preflight.models import (
    PreflightRequest,
    PrReference,
    RepositoryRef,
)
from slopolis_core.preflight.service import PreflightService

_USER = "octocat"


def _ref(
    full_name: str = "acme/api", *, private: bool = False, number: int = 1
) -> PrReference:
    _owner, name = full_name.split("/", 1)
    return PrReference(
        url=f"https://github.com/{full_name}/pull/{number}",
        repository=RepositoryRef(
            id=f"repo-{name}", full_name=full_name, private=private
        ),
        number=number,
        title=f"{full_name} PR {number}",
    )


class FakeGateway:
    """In-memory GitHubGateway for tests."""

    def __init__(
        self,
        *,
        refs: dict[str, PrReference] | None = None,
        covered: list[str] | None = None,
        access: set[str] | None = None,
        files: dict[tuple[str, str], str] | None = None,
    ) -> None:
        self.refs = refs or {}
        self.covered = covered if covered is not None else ["acme/api", "acme/web"]
        self.access = access
        self.files = files or {}

    async def resolve_pr(self, url: str) -> PrReference:
        if url not in self.refs:
            raise LookupError(url)
        return self.refs[url]

    async def list_covered_repos(self) -> list[str]:
        return list(self.covered)

    async def user_has_access(
        self, repo_full_name: str, *, private: bool, user_login: str
    ) -> bool:
        if self.access is None:
            return True
        return repo_full_name in self.access

    async def read_repo_file(self, repo_full_name: str, path: str) -> str | None:
        return self.files.get((repo_full_name, path))


class FakeWorkspace:
    """In-memory WorkspaceConfigProvider for tests."""

    def __init__(
        self,
        *,
        model: tuple[str, str] | None = ("gpt-4o", "litellm"),
        credential: bool = True,
    ) -> None:
        self.model = model
        self.credential = credential

    async def default_model(self) -> tuple[str, str] | None:
        return self.model

    async def credential_ready(self) -> bool:
        return self.credential

    async def model_assigned(self, role: str) -> tuple[str, str] | None:
        assert role == "review"
        return self.model


class FakeLiveCheck:
    """Records calls; optionally raises LlmError."""

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[str] = []

    async def check(self, model: str) -> None:
        self.calls.append(model)
        if self.fail:
            raise LlmError("model unavailable")


def _service(
    gateway: FakeGateway,
    *,
    workspace: FakeWorkspace | None = None,
    live_check: FakeLiveCheck | None = None,
) -> tuple[PreflightService, FakeLiveCheck]:
    check = live_check or FakeLiveCheck()
    return (
        PreflightService(gateway, workspace or FakeWorkspace(), check),
        check,
    )


async def test_valid_and_invalid_split() -> None:
    """Given one resolvable and one unknown link, they split valid/invalid."""
    ref = _ref()
    gateway = FakeGateway(refs={ref.url: ref})
    service, _ = _service(gateway)

    outcome = await service.run(
        PreflightRequest(pr_urls=[ref.url, "https://github.com/acme/api/pull/99"]),
        user_login=_USER,
    )

    assert [entry.url for entry in outcome.valid] == [ref.url]
    assert outcome.invalid == ["https://github.com/acme/api/pull/99"]
    assert any("Could not resolve" in note for note in outcome.notices)


async def test_duplicate_link_is_deduped_with_notice() -> None:
    """Given a repeated link, the duplicate is dropped and noted."""
    ref = _ref()
    gateway = FakeGateway(refs={ref.url: ref})
    service, _ = _service(gateway)

    outcome = await service.run(
        PreflightRequest(pr_urls=[ref.url, ref.url]), user_login=_USER
    )

    assert len(outcome.valid) == 1
    assert any("Duplicate link ignored" in note for note in outcome.notices)


async def test_uncovered_repo_is_invalid() -> None:
    """Given a repo outside the installation, the link is invalid."""
    ref = _ref("other/repo")
    gateway = FakeGateway(refs={ref.url: ref}, covered=["acme/api"])
    service, _ = _service(gateway)

    outcome = await service.run(PreflightRequest(pr_urls=[ref.url]), user_login=_USER)

    assert outcome.valid == []
    assert outcome.invalid == [ref.url]
    assert any("not covered" in note for note in outcome.notices)


async def test_access_denied_is_invalid_with_actionable_notice() -> None:
    """Given the user lacks access, the link is invalid with a clear notice."""
    ref = _ref(private=True)
    gateway = FakeGateway(refs={ref.url: ref}, access=set())
    service, _ = _service(gateway)

    outcome = await service.run(PreflightRequest(pr_urls=[ref.url]), user_login=_USER)

    assert outcome.invalid == [ref.url]
    assert any("private repos need read access" in note for note in outcome.notices)


async def test_missing_model_short_circuits_with_notice() -> None:
    """Given no assigned model, no target is valid and a notice explains it."""
    ref = _ref()
    gateway = FakeGateway(refs={ref.url: ref})
    service, check = _service(gateway, workspace=FakeWorkspace(model=None))

    outcome = await service.run(PreflightRequest(pr_urls=[ref.url]), user_login=_USER)

    assert outcome.valid == []
    assert outcome.invalid == []
    assert any("No review model is assigned" in note for note in outcome.notices)
    assert check.calls == []


async def test_missing_credential_short_circuits_with_notice() -> None:
    """Given no ready credential, no target is valid and a notice explains it."""
    ref = _ref()
    gateway = FakeGateway(refs={ref.url: ref})
    service, _ = _service(
        gateway, workspace=FakeWorkspace(credential=False)
    )

    outcome = await service.run(PreflightRequest(pr_urls=[ref.url]), user_login=_USER)

    assert outcome.valid == []
    assert any("No usable provider credential" in note for note in outcome.notices)


async def test_repo_config_parse_error_makes_repo_invalid() -> None:
    """Given a malformed .codereview.yml, the repo is invalid with the error."""
    ref = _ref()
    gateway = FakeGateway(
        refs={ref.url: ref},
        files={("acme/api", ".codereview.yml"): "review: [unclosed"},
    )
    service, _ = _service(gateway)

    outcome = await service.run(PreflightRequest(pr_urls=[ref.url]), user_login=_USER)

    assert outcome.invalid == [ref.url]
    assert any("acme/api" in note for note in outcome.notices)


async def test_repo_config_warnings_become_notices() -> None:
    """Given unknown config keys, warnings are surfaced as notices."""
    ref = _ref()
    gateway = FakeGateway(
        refs={ref.url: ref},
        files={("acme/api", ".codereview.yml"): "mystery_key: 1\n"},
    )
    service, _ = _service(gateway)

    outcome = await service.run(PreflightRequest(pr_urls=[ref.url]), user_login=_USER)

    assert [entry.url for entry in outcome.valid] == [ref.url]
    assert any("Unknown key 'mystery_key'" in note for note in outcome.notices)


async def test_live_check_failure_invalidates_targets() -> None:
    """Given the live model check fails, targets are invalid and it runs once."""
    ref_a = _ref("acme/api", number=1)
    ref_b = _ref("acme/web", number=2)
    gateway = FakeGateway(refs={ref_a.url: ref_a, ref_b.url: ref_b})
    service, check = _service(gateway, live_check=FakeLiveCheck(fail=True))

    outcome = await service.run(
        PreflightRequest(pr_urls=[ref_a.url, ref_b.url]), user_login=_USER
    )

    assert outcome.valid == []
    assert set(outcome.invalid) == {ref_a.url, ref_b.url}
    assert check.calls == ["gpt-4o"]
    assert any("Live model check failed" in note for note in outcome.notices)


async def test_wire_camel_case_and_python_names_both_parse() -> None:
    """Given camelCase wire input, models accept it and expose snake_case."""
    request = PreflightRequest.model_validate({"prUrls": ["https://x/1"]})
    assert request.pr_urls == ["https://x/1"]

    repo = RepositoryRef.model_validate(
        {"id": "1", "fullName": "acme/api", "private": False, "defaultBranch": "main"}
    )
    assert repo.full_name == "acme/api"
    assert repo.default_branch == "main"

    assert parse_repo_config("") is not None
    with pytest.raises(RepoConfigError):
        parse_repo_config("review: [unclosed")
