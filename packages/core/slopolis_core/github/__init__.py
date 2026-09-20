"""GitHub integration: typed client, publisher, models, and errors."""

from slopolis_core.github.app_installations import AppInstallations
from slopolis_core.github.client import (
    MAX_DIFF_LINES,
    MAX_FILE_BYTES,
    MAX_FILES,
    MAX_TOOL_CALLS,
    GitHubClient,
    ToolBudget,
)
from slopolis_core.github.errors import (
    GitHubAuthError,
    GitHubError,
    GitHubNotFoundError,
    GitHubRateLimitError,
)
from slopolis_core.github.models import (
    AppInstallation,
    ChangedFile,
    CheckRun,
    GitHubIssue,
    GitHubPullRequest,
    GitHubRepository,
    InlineComment,
    InstallationRepository,
)
from slopolis_core.github.publisher import GitHubPublisher

__all__ = [
    "MAX_DIFF_LINES",
    "MAX_FILES",
    "MAX_FILE_BYTES",
    "MAX_TOOL_CALLS",
    "AppInstallation",
    "AppInstallations",
    "ChangedFile",
    "CheckRun",
    "GitHubAuthError",
    "GitHubClient",
    "GitHubError",
    "GitHubIssue",
    "GitHubNotFoundError",
    "GitHubPublisher",
    "GitHubPullRequest",
    "GitHubRateLimitError",
    "GitHubRepository",
    "InlineComment",
    "InstallationRepository",
    "ToolBudget",
]
