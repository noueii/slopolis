"""A tiny time-to-live cache for reads that are expensive but may lag.

GitHub reports diff size and check state per pull request only, so the picker's
listing costs one read per pull request and one read per repository. Serving a
few seconds of stale data instead of paying that on every mount trades
freshness for GitHub calls and rate limit.

Deliberately minimal: no threads and no asyncio primitives. The API server is
single-threaded async, so ``get``/``put`` are synchronous and atomic (they never
await), and a caller cannot observe a half-written entry. Lifetimes are measured
with a monotonic clock, so a system clock change can never revive an entry.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Hashable
from dataclasses import dataclass

__all__ = ["TTLCache"]


@dataclass(frozen=True, slots=True)
class _Entry[V]:
    """A cached value together with the monotonic time it goes stale."""

    value: V
    expires_at: float


class TTLCache[K: Hashable, V]:
    """A bounded cache whose entries expire a fixed number of seconds after write.

    ``get`` never returns an expired value: it drops the entry instead, so a
    stale entry costs nothing until it is read. ``put`` refreshes the lifetime of
    an existing key and evicts the oldest entry once ``max_entries`` is reached.
    Cached values must not be ``None``: ``None`` is the miss signal.
    """

    def __init__(
        self,
        *,
        ttl_seconds: float,
        max_entries: int = 256,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be at least 1")
        self._ttl_seconds = ttl_seconds
        self._max_entries = max_entries
        self._clock = clock
        self._entries: dict[K, _Entry[V]] = {}

    def get(self, key: K) -> V | None:
        """Return the live value for ``key``, or ``None`` when absent or stale."""
        entry = self._entries.get(key)
        if entry is None:
            return None
        if entry.expires_at <= self._clock():
            del self._entries[key]
            return None
        return entry.value

    def put(self, key: K, value: V) -> None:
        """Store ``value`` under ``key`` and restart its lifetime."""
        self._entries.pop(key, None)
        if len(self._entries) >= self._max_entries:
            del self._entries[next(iter(self._entries))]
        self._entries[key] = _Entry(
            value=value, expires_at=self._clock() + self._ttl_seconds
        )

    def clear(self) -> None:
        """Drop every entry, live or stale."""
        self._entries.clear()

    def __len__(self) -> int:
        """Entries held, including stale ones not yet read."""
        return len(self._entries)
