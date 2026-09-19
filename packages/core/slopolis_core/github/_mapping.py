"""Pure translation helpers for the GitHub client.

Everything here is stateless: HTTP responses become typed models, GitHub
permission strings become a boolean policy decision, and oversized payloads
are truncated. Keeping these out of :mod:`slopolis_core.github.client` lets the
client stay a thin, testable request/response shell.
"""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Protocol, cast

from slopolis_core.github.errors import (
    GitHubAuthError,
    GitHubError,
    GitHubNotFoundError,
    GitHubRateLimitError,
)

__all__ = [
    "StatusResponse",
    "decode_content",
    "is_rate_limited",
    "iso",
    "parse_expiry",
    "permission_satisfies",
    "raise_for_status",
    "split_repo",
    "truncate_bytes",
    "truncate_lines",
]


class StatusResponse(Protocol):
    """Structural view of a githubkit ``Response`` used for status handling.

    githubkit's ``Response`` is invariant in its two type parameters, so a
    ``Response[Issue, ...]`` is not assignable to ``Response[object, object]``.
    This protocol names only what :func:`raise_for_status` needs, keeping it
    usable for every concrete response type.
    """

    @property
    def status_code(self) -> int: ...

    @property
    def is_success(self) -> bool: ...

    @property
    def headers(self) -> Mapping[str, str]: ...

    def json(self) -> object: ...


class FileContent(Protocol):
    """Structural view of a decoded file-content payload."""

    @property
    def type(self) -> str: ...

    @property
    def path(self) -> str: ...

    @property
    def encoding(self) -> str: ...

    @property
    def content(self) -> str | None: ...


def split_repo(repo_full_name: str) -> tuple[str, str]:
    """Split ``owner/repo`` into its parts, raising on a malformed name."""
    owner, _, repo = repo_full_name.partition("/")
    if not owner or not repo:
        raise GitHubNotFoundError(f"Not a valid repository name: {repo_full_name!r}")
    return owner, repo


def raise_for_status(response: StatusResponse, what: str) -> None:
    """Translate an error response into the matching typed error."""
    status = response.status_code
    if response.is_success:
        return
    if status == 404:
        raise GitHubNotFoundError(f"GitHub resource not found: {what}")
    if status in (401, 403) and is_rate_limited(response):
        raise GitHubRateLimitError(f"GitHub rate limit hit while fetching {what}")
    if status in (401, 403):
        raise GitHubAuthError(f"GitHub authorization failed for {what} (HTTP {status})")
    if status == 429:
        raise GitHubRateLimitError(f"GitHub rate limit hit while fetching {what}")
    raise GitHubError(f"GitHub request failed for {what} (HTTP {status})")


def is_rate_limited(response: StatusResponse) -> bool:
    """Detect a rate-limit 403 by header or body message."""
    if response.headers.get("x-ratelimit-remaining") == "0":
        return True
    try:
        payload = response.json()
    except json.JSONDecodeError:
        return False
    if not isinstance(payload, dict):
        return False
    message = cast("dict[str, object]", payload).get("message")
    return isinstance(message, str) and "rate limit" in message.lower()


def decode_content(data: FileContent) -> str:
    """Decode a base64 content file, or return the text payload as-is."""
    raw = data.content or ""
    if data.encoding == "base64":
        try:
            return base64.b64decode(raw, validate=False).decode("utf-8", errors="replace")
        except (binascii.Error, ValueError) as exc:
            raise GitHubError(f"Could not decode content for {data.path}: {exc}") from exc
    return raw


def truncate_bytes(text: str, limit: int) -> str:
    """Truncate ``text`` to ``limit`` bytes, appending a marker when clipped."""
    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return text
    clipped = encoded[:limit].decode("utf-8", errors="ignore")
    return f"{clipped}\n\n... [truncated at {limit} bytes]"


def truncate_lines(text: str, limit: int) -> tuple[str, bool]:
    """Truncate ``text`` to ``limit`` lines; return text and whether clipped."""
    lines = text.splitlines()
    if len(lines) <= limit:
        return text, False
    return "\n".join(lines[:limit]) + f"\n... [truncated at {limit} lines]", True


def iso(value: datetime | None) -> str:
    """Render a datetime as ISO-8601, defaulting to an empty string."""
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat()


def parse_expiry(value: object) -> float:
    """Convert an installation-token ``expires_at`` to epoch seconds.

    GitHub returns an ISO-8601 string; githubkit's schema types it as ``str``.
    A datetime is accepted too so the helper survives either representation.
    """
    if isinstance(value, datetime):
        expires = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        return expires.timestamp()
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise GitHubError(f"Unparseable token expiry {value!r}") from exc
        return (parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)).timestamp()
    raise GitHubError(f"Unsupported token expiry type: {type(value).__name__}")


def permission_satisfies(permission: str, *, required: str) -> bool:
    """Map a GitHub permission string to whether it meets ``required``."""
    ranks = {"none": 0, "read": 1, "triage": 1, "write": 2, "maintain": 3, "admin": 4}
    return ranks.get(permission.lower(), 0) >= ranks[required]
