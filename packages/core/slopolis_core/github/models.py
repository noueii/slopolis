"""Strict wire models for the GitHub REST API surface slopolis consumes.

Each model mirrors exactly the fields the review harness and publisher need
(spec 10.6/10.7). ``extra="forbid"`` makes an unexpected API field a loud
failure instead of silent drift, and JSON payloads are parsed here at the
boundary once; interior code receives typed values.
"""

from dataclasses import dataclass

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
    "ReviewComment",
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
    #: When the pull request was opened. The inbox's ``created_desc`` sort orders
    #: on it (spec v3 §3); empty for a caller that never mapped it.
    created_at: str = ""


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


@dataclass(frozen=True, slots=True)
class ReviewComment:
    """A review comment this App has on a pull request, as GitHub describes it.

    Both inline paths end here — a comment the publisher just created and one
    reconciliation adopted from the pull request are the same fact — so they share
    one shape: ``id`` is what a caller stamps a finding with, and ``diff_hunk`` is
    GitHub's own hunk text for the comment, the ``@@ … @@`` header and its lines,
    which is what the app needs to render the comment the way GitHub does.
    ``diff_hunk`` is ``None`` when GitHub returned no hunk for the comment.
    """

    id: int
    diff_hunk: str | None


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
