"""Strict wire models for the GitHub REST API surface slopolis consumes.

Each model mirrors exactly the fields the review harness and publisher need
(spec 10.6/10.7). ``extra="forbid"`` makes an unexpected API field a loud
failure instead of silent drift, and JSON payloads are parsed here at the
boundary once; interior code receives typed values.
"""

from pydantic import BaseModel, ConfigDict

__all__ = [
    "AppInstallation",
    "ChangedFile",
    "CheckRun",
    "GitHubIssue",
    "GitHubPullRequest",
    "GitHubRepository",
    "InlineComment",
    "InstallationRepository",
]

_STRICT = ConfigDict(extra="forbid")


class GitHubPullRequest(BaseModel):
    """A pull request resolved from a GitHub URL or the REST API."""

    model_config = _STRICT

    repo_full_name: str
    private: bool
    number: int
    title: str
    url: str
    head_branch: str
    base_branch: str
    head_sha: str
    default_branch: str
    body: str
    author_login: str
    draft: bool
    changed_files: int
    additions: int
    deletions: int
    updated_at: str


class GitHubRepository(BaseModel):
    """A repository the app installation can access."""

    model_config = _STRICT

    id: int
    full_name: str
    private: bool
    default_branch: str
    open_pr_count: int
    last_activity_at: str


class CheckRun(BaseModel):
    """One CI check run attached to a commit — the read side of a CI rollup.

    ``status`` is the run's lifecycle state (`queued`, `in_progress`, `completed`)
    and ``conclusion`` is set once it completes (`success`, `failure`, `neutral`,
    `skipped`, `cancelled`, `timed_out`, `action_required`).
    """

    model_config = _STRICT

    name: str
    status: str
    conclusion: str | None = None


class GitHubIssue(BaseModel):
    """An issue body fetched for context."""

    model_config = _STRICT

    number: int
    title: str
    body: str
    state: str


class ChangedFile(BaseModel):
    """One file entry in a pull request's changed-file list."""

    model_config = _STRICT

    path: str
    additions: int
    deletions: int
    status: str


class InlineComment(BaseModel):
    """A single inline review comment payload (spec 10.7)."""

    model_config = _STRICT

    path: str
    line: int
    body: str


class AppInstallation(BaseModel):
    """A GitHub App installation on a user, organization, or enterprise account."""

    model_config = _STRICT

    installation_id: int
    account_login: str
    account_type: str
    repository_selection: str
    suspended: bool


class InstallationRepository(BaseModel):
    """A repository an installation granted the App access to."""

    model_config = _STRICT

    github_id: int
    full_name: str
    private: bool
    default_branch: str
