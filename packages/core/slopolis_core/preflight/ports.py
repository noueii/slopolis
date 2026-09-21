"""Consumer-side ports for pre-flight dependencies (spec 10.3).

These Protocols are implemented by the GitHub lane and the workspace/database
layer. Defining them here (consumer side) lets pre-flight depend on structure —
a fake GitHub is enough in tests — rather than on the concrete
``slopolis_core.github.client.GitHubClient``; the only thing it takes from that
package is the pure publish-permission helper. Tests supply fakes.
"""

from typing import Protocol, runtime_checkable

from slopolis_core.llm.client import LlmClient
from slopolis_core.llm.models import ChatMessage
from slopolis_core.preflight.models import PrReference

__all__ = [
    "GitHubGateway",
    "LiveModelCheck",
    "LlmLiveModelCheck",
    "WorkspaceConfigProvider",
]


@runtime_checkable
class GitHubGateway(Protocol):
    """Read-only GitHub operations pre-flight and review harness need."""

    async def resolve_pr(self, url: str) -> PrReference: ...

    async def list_covered_repos(self) -> list[str]: ...

    async def user_has_access(
        self,
        repo_full_name: str,
        *,
        private: bool,
        user_login: str,
        required: str | None = None,
    ) -> bool: ...

    async def read_repo_file(self, repo_full_name: str, path: str) -> str | None: ...

    async def publish_permissions(self, repo_full_name: str) -> dict[str, str] | None:
        """Return the permissions ``repo_full_name``'s installation was granted.

        Keys are GitHub's permission names, values its levels; a scope the
        installation was not granted is absent. Which of them publishing needs
        and which it can do without is decided by
        :func:`slopolis_core.github.permissions.missing_required_scopes` and
        :func:`slopolis_core.github.permissions.missing_optional_scopes` in the
        service, not here: the port stays a read of what the installation
        says about itself, so the refusal's vocabulary lives with the refusal.
        ``None`` means the answer could not be read at all — a failure pre-flight
        reports as a notice instead of failing the submission, the way a missing
        ``.codereview.yml`` is a ``None`` rather than an error.
        """
        ...


@runtime_checkable
class WorkspaceConfigProvider(Protocol):
    """Workspace model assignment, credential state, and access policy."""

    async def default_model(self) -> tuple[str, str] | None: ...

    async def credential_ready(self) -> bool: ...

    async def model_assigned(self, role: str) -> tuple[str, str] | None: ...

    async def required_access(self, repo_full_name: str) -> str | None:
        """Return the repository's access override, if any.

        ``None`` means the spec rule applies (private repos need read, public
        repos need write); ``"read"``/``"write"`` overrides it.
        """
        ...


@runtime_checkable
class LiveModelCheck(Protocol):
    """A lightweight o1-token call proving the model answers (spec 10.3)."""

    async def check(self, model: str) -> None: ...


class LlmLiveModelCheck:
    """Default :class:`LiveModelCheck` backed by a one-token completion."""

    def __init__(self, llm: LlmClient) -> None:
        self._llm = llm

    async def check(self, model: str) -> None:
        """Run a ``ping`` completion, raising :class:`LlmError` on failure."""
        await self._llm.complete(
            [ChatMessage(role="user", content="ping")],
            model=model,
            max_tokens=1,
        )
