"""Budget, cache, and cap types shared by the GitHub client.

Kept separate from :mod:`slopolis_core.github.client` so the client module stays
focused on request/response orchestration and under the file-size ceiling.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from slopolis_core.github.errors import GitHubError
from slopolis_core.github.models import ChangedFile, GitHubIssue, GitHubPullRequest

__all__ = [
    "MAX_DIFF_LINES",
    "MAX_FILES",
    "MAX_FILE_BYTES",
    "MAX_TOOL_CALLS",
    "PR_URL_RE",
    "ReadCache",
    "RepoMeta",
    "ToolBudget",
]

#: Hard cap on bytes returned by ``GitHubClient.read_file``.
MAX_FILE_BYTES = 200_000
#: Hard cap on unified-diff lines embedded in a :class:`PrContext`.
MAX_DIFF_LINES = 20_000
#: Hard cap on changed files embedded in a :class:`PrContext`.
MAX_FILES = 50
#: Hard cap on tool calls a single review job may spend.
MAX_TOOL_CALLS = 100

#: Matches ``https://github.com/{owner}/{repo}/pull/{n}``.
PR_URL_RE = re.compile(
    r"^https?://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/pull/(?P<number>\d+)/?$"
)


@dataclass
class ToolBudget:
    """Counts tool calls against a hard cap and fails closed when exhausted."""

    limit: int = MAX_TOOL_CALLS
    used: int = 0

    def consume(self, *, method: str) -> None:
        """Charge one call; raise :class:`GitHubError` once the cap is hit."""
        if self.used >= self.limit:
            raise GitHubError(
                f"Tool budget exhausted after {self.limit} calls (at {method}); "
                "fall back to diff-only review"
            )
        self.used += 1

    @property
    def remaining(self) -> int:
        """Calls left before the budget is exhausted."""
        return max(self.limit - self.used, 0)


@dataclass
class ReadCache:
    """Per-instance memo so repeated tool calls in one job do not refetch."""

    pulls: dict[tuple[str, int], GitHubPullRequest] = field(default_factory=dict)
    files: dict[tuple[str, int], list[ChangedFile]] = field(default_factory=dict)
    contents: dict[tuple[str, str, str], str] = field(default_factory=dict)
    dirs: dict[tuple[str, str, str], list[str]] = field(default_factory=dict)
    issues: dict[tuple[str, int], GitHubIssue] = field(default_factory=dict)
    open_pulls: dict[str, list[GitHubPullRequest]] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RepoMeta:
    """Privacy and default branch for a repository, fetched once per call."""

    private: bool
    default_branch: str
