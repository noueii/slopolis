"""Consumer-side ports for pre-flight dependencies (spec 10.3).

These Protocols are implemented later by the GitHub lane and the
workspace/database layer. Defining them here (consumer side) lets pre-flight
depend on structure, not on ``slopolis_core.github``, which is owned by
another lane. Tests supply fakes.
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
