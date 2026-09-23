"""Synchronous pre-flight validation pipeline (spec 10.3).

Runs on submit, before any session is created. It parses and dedupes links,
resolves each PR, checks repo coverage and trigger access, checks the
installation may write what publishing needs, verifies the workspace has an
assigned model and a ready credential, performs a single cached live model
check, and validates ``.codereview.yml``.

Expected validation failures never raise: they land in the outcome's
``invalid``/``notices`` fields. Only programming errors propagate.
"""

from collections.abc import Sequence

from slopolis_core.config.repo_config import RepoConfigError, parse_repo_config
from slopolis_core.github.permissions import (
    PUBLISH_SCOPE_LABELS,
    missing_optional_scopes,
    missing_required_scopes,
)
from slopolis_core.llm.client import LlmError
from slopolis_core.preflight.models import (
    PreflightOutcome,
    PreflightRequest,
    PrReference,
)
from slopolis_core.preflight.ports import (
    GitHubGateway,
    LiveModelCheck,
    WorkspaceConfigProvider,
)

__all__ = ["PreflightService"]

_REPO_CONFIG_PATH = ".codereview.yml"


def _access_refusal(full_name: str) -> str:
    """The refusal notice for a denied trigger, naming the spec rule."""
    return (
        f"You lack the required access to {full_name}; "
        "private repos need read access, public repos need write access."
    )


def _label_list(scopes: Sequence[str]) -> str:
    """Name scopes the way GitHub's App settings do, as one clause."""
    names = [PUBLISH_SCOPE_LABELS[scope] for scope in scopes]
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"


def _publish_refusal(full_name: str, missing: Sequence[str]) -> str:
    """The refusal notice for an installation that cannot publish (spec 10.3).

    Names the scopes the way GitHub's App settings do and states the fix, in the
    wording the worker's publish backstop uses: a permission revoked between
    submit and publish must read as the same problem as one missing from the
    start. ``missing`` arrives in :data:`PUBLISH_SCOPES` order, so the sentence is
    stable for a given set of scopes.
    """
    return (
        f"The GitHub App installation for {full_name} cannot write: "
        f"grant {_label_list(missing)} 'Read & write' on the App, "
        "then approve the update for the installation."
    )


def _check_run_skipped_notice(full_name: str, missing: Sequence[str]) -> str:
    """The notice for an advisory write a missing grant will skip (spec 10.3).

    Deliberately not a refusal: the check run is advisory (overview §10) and
    posted last, so the review still reaches the pull request as a comment. The
    wording says so — the user is giving up the check run, not the review — and
    still names the scope and the fix, so the loss can be undone deliberately
    instead of puzzling over a review that never showed up as a check.
    """
    listed = _label_list(missing)
    return (
        f"The GitHub App installation for {full_name} cannot write {listed}: "
        "the review will post without a check run. "
        f"Grant {listed} 'Read & write' on the App, then approve the update for "
        "the installation to add it."
    )


def _unreadable_publish_refusal(full_name: str) -> str:
    """The refusal notice when the installation's permissions cannot be read."""
    return (
        f"Could not check whether the GitHub App installation can write to "
        f"{full_name}; publishing cannot be guaranteed. Try again."
    )


class _RunState:
    """Mutable accumulator for one pre-flight run."""

    def __init__(self, request: PreflightRequest) -> None:
        self.valid: list[PrReference] = []
        self.invalid: list[str] = []
        self.notices: list[str] = []
        self.live_check_passed: bool | None = None
        seen: set[str] = set()
        deduped: list[str] = []
        for raw in request.pr_urls:
            url = raw.strip()
            if not url:
                self.invalid.append(raw)
                continue
            if url in seen:
                self.notices.append(f"Duplicate link ignored: {url}")
                continue
            seen.add(url)
            deduped.append(url)
        self.urls: list[str] = deduped

    def outcome(self) -> PreflightOutcome:
        return PreflightOutcome(
            valid=self.valid, invalid=self.invalid, notices=self.notices
        )


class _RunContext:
    """Immutable per-run inputs threaded through per-target validation."""

    def __init__(
        self,
        *,
        gateway: GitHubGateway,
        workspace: WorkspaceConfigProvider,
        state: _RunState,
        covered: set[str],
        model_id: str,
        user_login: str,
        live_check: LiveModelCheck,
    ) -> None:
        self.gateway = gateway
        self.workspace = workspace
        self.state = state
        self.covered = covered
        self.model_id = model_id
        self.user_login = user_login
        self.live_check = live_check


class PreflightService:
    """Validate a New Review submission without creating a session."""

    def __init__(
        self,
        gateway: GitHubGateway,
        workspace: WorkspaceConfigProvider,
        live_check: LiveModelCheck,
    ) -> None:
        self._gateway = gateway
        self._workspace = workspace
        self._live_check = live_check

    async def run(
        self, request: PreflightRequest, *, user_login: str
    ) -> PreflightOutcome:
        """Return the validated outcome for ``request`` (never raises on input)."""
        state = _RunState(request)

        model = await self._workspace.model_assigned("review")
        if model is None:
            model = await self._workspace.default_model()
        if model is None:
            state.notices.append(
                "No review model is assigned; configure a model before submitting."
            )
            return state.outcome()

        model_id, _provider = model

        if not await self._workspace.credential_ready():
            state.notices.append(
                "No usable provider credential; add or re-enable a key in settings."
            )
            return state.outcome()

        covered = set(await self._gateway.list_covered_repos())
        context = _RunContext(
            gateway=self._gateway,
            workspace=self._workspace,
            state=state,
            covered=covered,
            model_id=model_id,
            user_login=user_login,
            live_check=self._live_check,
        )

        for url in state.urls:
            await self._validate_one(url, context)

        return state.outcome()

    async def _validate_one(self, url: str, context: _RunContext) -> None:
        """Resolve and fully validate one PR link, appending to run state."""
        state = context.state
        try:
            reference = await context.gateway.resolve_pr(url)
        except LookupError:
            state.invalid.append(url)
            state.notices.append(f"Could not resolve pull request: {url}")
            return

        full_name = reference.repository.full_name
        if full_name not in context.covered:
            state.invalid.append(url)
            state.notices.append(
                f"Repository {full_name} is not covered by the GitHub App installation."
            )
            return

        has_access = await context.gateway.user_has_access(
            full_name,
            private=reference.repository.private,
            user_login=context.user_login,
        )
        if not has_access:
            state.invalid.append(url)
            state.notices.append(_access_refusal(full_name))
            return

        if not await self._check_publish_permissions(full_name, context):
            state.invalid.append(url)
            return

        if not await self._check_live_model(context):
            state.invalid.append(url)
            return

        if not await self._load_repo_config(reference, context):
            state.invalid.append(url)
            return

        state.valid.append(reference)

    async def _check_publish_permissions(
        self, full_name: str, context: _RunContext
    ) -> bool:
        """Whether the installation may write what publishing needs (spec 10.3).

        Only a scope publishing cannot post the review without refuses the link;
        a scope it can do without (the advisory check run) becomes a notice, so
        the user learns what the review will skip instead of losing the
        submission to it. Runs before the live model check: an installation that
        cannot post the review must be refused before anything the submission
        pays for happens. The read is a fact about the installation, so the
        gateway is free to answer several links of one submission out of one
        call.
        """
        state = context.state
        permissions = await context.gateway.publish_permissions(full_name)
        if permissions is None:
            state.notices.append(_unreadable_publish_refusal(full_name))
            return False
        missing = missing_required_scopes(permissions)
        if missing:
            state.notices.append(_publish_refusal(full_name, missing))
            return False
        skipped = missing_optional_scopes(permissions)
        if skipped:
            state.notices.append(_check_run_skipped_notice(full_name, skipped))
        return True

    async def _check_live_model(self, context: _RunContext) -> bool:
        """Run the live model check once per run; reuse the cached result."""
        state = context.state
        if state.live_check_passed is not None:
            return state.live_check_passed
        try:
            await context.live_check.check(context.model_id)
        except LlmError as exc:
            state.notices.append(
                f"Live model check failed for {context.model_id}: {exc}."
            )
            state.live_check_passed = False
            return False
        state.live_check_passed = True
        return True

    async def _load_repo_config(
        self, reference: PrReference, context: _RunContext
    ) -> bool:
        """Fetch and parse ``.codereview.yml``; record errors and warnings."""
        state = context.state
        full_name = reference.repository.full_name
        text = await context.gateway.read_repo_file(full_name, _REPO_CONFIG_PATH)
        if text is None:
            return True
        try:
            parsed = parse_repo_config(text)
        except RepoConfigError as exc:
            state.notices.append(f"{full_name}: {exc}")
            return False
        state.notices.extend(f"{full_name}: {warning}" for warning in parsed.warnings)
        return True
