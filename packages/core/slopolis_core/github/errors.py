"""Typed errors for the GitHub API boundary (spec overview §4, features 06/07).

Every failure crossing the GitHub boundary is one of these types, so callers
can discriminate on the failure class instead of parsing HTTP status codes.
"""

__all__ = [
    "GitHubAuthError",
    "GitHubError",
    "GitHubNotFoundError",
    "GitHubRateLimitError",
]


class GitHubError(Exception):
    """Base class for every failure raised by the GitHub client."""


class GitHubAuthError(GitHubError):
    """Authentication or authorization with GitHub failed.

    Raised on 401/403 responses and when an installation token is missing or
    expired beyond refresh. Also signals that the caller lacks the permission
    required to perform the operation.
    """


class GitHubNotFoundError(GitHubError):
    """The requested resource (repo, PR, issue, file, ref) does not exist.

    Raised on 404 responses and for malformed resource identifiers such as an
    unparseable pull-request URL.
    """


class GitHubRateLimitError(GitHubError):
    """GitHub rate limit or abuse protection was hit.

    Raised on 429 responses and on 403 responses whose body or headers
    indicate a rate-limit exhaustion rather than a permission failure.
    """
