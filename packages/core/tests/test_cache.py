"""Tests for the generic TTL cache."""

import pytest

from slopolis_core.cache import TTLCache


def test_get_misses_an_absent_key() -> None:
    """Given an empty cache, reading any key returns None."""
    cache: TTLCache[str, int] = TTLCache(ttl_seconds=30.0)

    assert cache.get("acme/api") is None
    assert len(cache) == 0


def test_put_then_get_returns_the_value() -> None:
    """Given a stored value, reading its key returns it."""
    cache: TTLCache[str, int] = TTLCache(ttl_seconds=30.0)

    cache.put("acme/api", 3)

    assert cache.get("acme/api") == 3
    assert len(cache) == 1


def test_entry_is_dropped_once_its_ttl_passes() -> None:
    """Given an injected clock, an entry is live until its TTL and stale after."""
    now = [1_000.0]
    cache: TTLCache[str, int] = TTLCache(ttl_seconds=30.0, clock=lambda: now[0])
    cache.put("acme/api", 3)

    now[0] = 1_029.0  # inside the window
    assert cache.get("acme/api") == 3

    now[0] = 1_030.0  # the deadline itself is already stale
    assert cache.get("acme/api") is None
    # The stale entry is evicted by the read, not merely hidden.
    assert len(cache) == 0


def test_put_restarts_the_lifetime_of_a_live_key() -> None:
    """Given a refreshed key, its entry outlives the original deadline."""
    now = [1_000.0]
    cache: TTLCache[str, int] = TTLCache(ttl_seconds=30.0, clock=lambda: now[0])
    cache.put("acme/api", 1)

    now[0] = 1_020.0
    cache.put("acme/api", 2)

    now[0] = 1_045.0  # past the first deadline, inside the second
    assert cache.get("acme/api") == 2
    assert len(cache) == 1


def test_oldest_entry_is_evicted_at_capacity() -> None:
    """Given a full cache, storing a new key drops the oldest entry."""
    cache: TTLCache[str, int] = TTLCache(ttl_seconds=30.0, max_entries=2)
    cache.put("a", 1)
    cache.put("b", 2)

    cache.put("c", 3)

    assert cache.get("a") is None
    assert (cache.get("b"), cache.get("c")) == (2, 3)
    assert len(cache) == 2


def test_clear_drops_every_entry() -> None:
    """Given a populated cache, clearing leaves no entries behind."""
    cache: TTLCache[str, int] = TTLCache(ttl_seconds=30.0)
    cache.put("a", 1)
    cache.put("b", 2)

    cache.clear()

    assert len(cache) == 0
    assert cache.get("a") is None


def test_capacity_below_one_is_rejected() -> None:
    """Given a nonsensical capacity, construction fails instead of evicting oddly."""
    with pytest.raises(ValueError):
        TTLCache[str, int](ttl_seconds=30.0, max_entries=0)
