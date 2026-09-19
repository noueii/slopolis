"""Installation token custody for GitHub API calls.

splits the two pieces the client needs: the installation id (for minting) and
the short-lived installation access token (for calling). The token cache keys
minted tokens by installation id and honors GitHub's ``expires_at`` so a
long-running worker reuses a token until it is near expiry.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

__all__ = ["InstallationAuth", "TokenCache", "TokenCacheEntry"]

#: Refresh a cached token this many seconds before its real expiry.
_EXPIRY_SKEW_SECONDS = 60


@dataclass(frozen=True, slots=True)
class InstallationAuth:
    """An installation id paired with the installation access token for it."""

    installation_id: int | None
    token: str

    @classmethod
    def from_installation_token(cls, token: str) -> InstallationAuth:
        """Build auth from a token alone (installation id unknown)."""
        return cls(installation_id=None, token=token)


@dataclass(frozen=True, slots=True)
class TokenCacheEntry:
    """A cached installation token plus its absolute expiry (epoch seconds)."""

    token: str
    expires_at: float


@dataclass
class TokenCache:
    """In-memory installation-token cache with expiry.

    ``expires_at`` is stored as epoch seconds. A token is considered fresh
    until ``expires_at - skew``; callers that find a stale entry must mint a
    new token and call :meth:`store`.
    """

    _entries: dict[int, TokenCacheEntry] = field(default_factory=dict)

    def get(self, installation_id: int) -> str | None:
        """Return a fresh token for ``installation_id``, or ``None`` if stale."""
        entry = self._entries.get(installation_id)
        if entry is None:
            return None
        if entry.expires_at - _EXPIRY_SKEW_SECONDS <= time.time():
            return None
        return entry.token

    def store(self, installation_id: int, token: str, expires_at: float) -> None:
        """Cache a token minted for ``installation_id``."""
        self._entries[installation_id] = TokenCacheEntry(token=token, expires_at=expires_at)

    def clear(self) -> None:
        """Drop every cached token (used by tests and on installation revoke)."""
        self._entries.clear()
