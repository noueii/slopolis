"""Per-viewer repository access checks (spec 10.8 §Access).

Workspace membership is not repository access: the installation token sees every
repository the App was granted, which is strictly more than any one member may
read. Every read is therefore filtered by the **viewer's own** GitHub access,
using the token kept at sign-in (spec 10.1) — never the installation's.

A check has three outcomes, and the difference between them matters:

* ``True`` — GitHub says the user can read the repository.
* ``False`` — GitHub says no (403/404), so the content is simply hidden.
* ``None`` — the check could not be made at all: no stored token, a token the
  vault cannot open, a revoked token, or GitHub unreachable. That is **not** a
  grant, but it is not a denial either, so the API reports "unverified" instead
  of quietly showing less.

Verdicts are cached per ``(user, repo)`` for a short window, so a session list
costs one GitHub call per repository rather than one per row. An *unverifiable*
result is deliberately never cached: an outage or a token rotation must not be
remembered for the whole window.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable, Iterable
from typing import Protocol

import httpx

from slopolis_core.cache import TTLCache
from slopolis_db.models import User

__all__ = [
    "GitHubRepoProbe",
    "RepoAccessChecker",
    "RepoAccessUnavailable",
    "RepoVisibilityProbe",
    "TokenSource",
]

_GITHUB_REPOS_URL = "https://api.github.com/repos/{full_name}"
_PROBE_TIMEOUT_S = 10.0
_DEFAULT_TTL_SECONDS = 60.0
_DEFAULT_MAX_ENTRIES = 1024
_DEFAULT_CONCURRENCY = 8

#: Answers "which token do this user's checks run with?" — ``None`` when the
#: account holds no token the vault can open.
TokenSource = Callable[[User], str | None]


class RepoAccessUnavailable(Exception):
    """GitHub could not answer the check (transport failure, 5xx, rate limit)."""


class RepoVisibilityProbe(Protocol):
    """The one GitHub read a check needs, injectable so tests stay offline."""

    async def user_can_read(self, *, token: str, repo_full_name: str) -> bool:
        """Return whether ``token`` may read ``repo_full_name``.

        Raises :class:`RepoAccessUnavailable` when GitHub cannot answer.
        """
        ...


class GitHubRepoProbe:
    """``GET /repos/{owner}/{repo}`` with the user's own token.

    GitHub answers 200 for a repository the token can see, 404 for one it cannot
    (the private-repository privacy dance), and 403 for a public repository it
    must not read. A 401 (revoked token) and a rate-limited 403 mean the check
    could not be made, not that access was denied.
    """

    def __init__(self, *, client: httpx.AsyncClient | None = None) -> None:
        self._client = client

    async def user_can_read(self, *, token: str, repo_full_name: str) -> bool:
        url = _GITHUB_REPOS_URL.format(full_name=repo_full_name)
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        }
        try:
            if self._client is None:
                # The API server holds no long-lived user-scoped client, and this
                # read is cached for a minute, so a short-lived one is enough.
                async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT_S) as client:
                    response = await client.get(url, headers=headers)
            else:
                response = await self._client.get(url, headers=headers)
        except httpx.HTTPError as exc:
            raise RepoAccessUnavailable(f"{type(exc).__name__}: {exc}") from exc

        status = response.status_code
        if status == 200:
            return True
        if status == 401:
            raise RepoAccessUnavailable("the user's GitHub token is no longer valid")
        if status == 403 and _rate_limited(response):
            raise RepoAccessUnavailable("the GitHub rate limit is exhausted")
        if status in (403, 404):
            return False
        raise RepoAccessUnavailable(f"GitHub answered HTTP {status}")


def _rate_limited(response: httpx.Response) -> bool:
    """Whether a 403 is GitHub's rate limit rather than a refused repository."""
    return response.headers.get("x-ratelimit-remaining") == "0"


class RepoAccessChecker:
    """Answers "may this viewer read this repository?" with a short TTL cache."""

    def __init__(
        self,
        *,
        probe: RepoVisibilityProbe,
        tokens: TokenSource,
        cache: TTLCache[tuple[uuid.UUID, str], bool] | None = None,
        max_concurrency: int = _DEFAULT_CONCURRENCY,
    ) -> None:
        self._probe = probe
        self._tokens = tokens
        # ``or`` would be wrong here: a freshly built TTL cache is empty and
        # therefore falsy, so an injected one would be silently replaced.
        self._cache: TTLCache[tuple[uuid.UUID, str], bool] = (
            cache
            if cache is not None
            else TTLCache(ttl_seconds=_DEFAULT_TTL_SECONDS, max_entries=_DEFAULT_MAX_ENTRIES)
        )
        self._max_concurrency = max_concurrency

    async def can_read(self, user: User, repo_full_name: str) -> bool | None:
        """Return ``True``, ``False``, or ``None`` (unverifiable) for one repository."""
        verdicts = await self.can_read_many(user, [repo_full_name])
        return verdicts[repo_full_name]

    async def can_read_many(
        self, user: User, repo_full_names: Iterable[str]
    ) -> dict[str, bool | None]:
        """Return one verdict per repository, with one check per distinct name.

        Checks run concurrently under a small bound and anything already cached
        costs nothing, so a page of sessions is one GitHub round trip per
        repository — not one per row and not one per session.
        """
        names = list(dict.fromkeys(repo_full_names))
        verdicts: dict[str, bool | None] = {}
        pending: list[str] = []
        for name in names:
            cached = self._cache.get((user.id, name))
            if cached is None:
                pending.append(name)
            else:
                verdicts[name] = cached
        if not pending:
            return verdicts

        token = self._tokens(user)
        if token is None:
            for name in pending:
                verdicts[name] = None
            return verdicts

        verdicts.update(await self._check_all(user.id, token, pending))
        return verdicts

    async def _check_all(
        self, user_id: uuid.UUID, token: str, names: list[str]
    ) -> dict[str, bool | None]:
        """Probe every name concurrently and cache each determinate verdict."""
        bound = asyncio.Semaphore(self._max_concurrency)

        async def one(name: str) -> tuple[str, bool | None]:
            async with bound:
                try:
                    return name, await self._probe.user_can_read(
                        token=token, repo_full_name=name
                    )
                except RepoAccessUnavailable:
                    return name, None

        pairs = await asyncio.gather(*(one(name) for name in names))
        verdicts: dict[str, bool | None] = {}
        for name, verdict in pairs:
            if verdict is not None:
                self._cache.put((user_id, name), verdict)
            verdicts[name] = verdict
        return verdicts
