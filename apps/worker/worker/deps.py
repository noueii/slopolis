"""Worker dependency factories and injectable ports (spec 10.5).

Everything the job touches outside the database is constructed here so tests
can substitute fakes. The job depends on the :class:`ReviewContext` bundle and
the protocols in this module; production wiring lives in the ``build_*``
factories, and the ARQ ``on_startup`` hook assembles one context per worker.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from typing import Protocol, runtime_checkable

import githubkit.auth
from githubkit import GitHub, TokenAuthStrategy
from sqlalchemy.ext.asyncio import AsyncSession

from slopolis_core.context import PrContext
from slopolis_core.github._mapping import parse_expiry, raise_for_status
from slopolis_core.github.auth import TokenCache
from slopolis_core.github.client import GitHubClient
from slopolis_core.github.errors import GitHubAuthError
from slopolis_core.github.models import GitHubPullRequest, InlineComment
from slopolis_core.github.publisher import GitHubPublisher
from slopolis_core.llm.client import LiteLlmClient, LlmClient
from slopolis_core.review.harness import ReviewHarness
from slopolis_core.settings import get_settings
from slopolis_db.session import get_session
from worker.config import WorkerConfig

__all__ = [
    "ContextReader",
    "InstallationClients",
    "InstallationRef",
    "Publisher",
    "ReviewContext",
    "SessionFactory",
    "build_github_client",
    "build_installation_clients",
    "build_llm_client",
    "build_publisher",
    "db_session_factory",
]

#: A callable returning an async-context-managed database session.
SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


@runtime_checkable
class ContextReader(Protocol):
    """Read surface the job needs from GitHub (subset of :class:`GitHubClient`)."""

    def get_pr_context(self, repo_full_name: str, number: int) -> Awaitable[PrContext]: ...

    def get_pull_request(
        self, repo_full_name: str, number: int
    ) -> Awaitable[GitHubPullRequest]: ...

    def read_file(self, repo_full_name: str, path: str, ref: str) -> Awaitable[str]: ...


@runtime_checkable
class Publisher(Protocol):
    """Publish surface the job needs (subset of :class:`GitHubPublisher`)."""

    def upsert_summary_comment(
        self,
        repo_full_name: str,
        number: int,
        body: str,
        existing_comment_id: int | None,
    ) -> Awaitable[int]: ...

    def post_inline_comments(
        self,
        repo_full_name: str,
        number: int,
        comments: list[InlineComment],
        commit_id: str,
    ) -> Awaitable[list[int]]: ...

    def upsert_check_run(
        self,
        repo_full_name: str,
        head_sha: str,
        *,
        conclusion: str,
        title: str,
        summary: str,
    ) -> Awaitable[int]: ...


class _ReaderAdapter:
    """Adapt a :class:`ContextReader` to the harness's coroutine protocol."""

    def __init__(self, reader: ContextReader) -> None:
        self._reader = reader

    async def get_pr_context(self, repo_full_name: str, number: int) -> PrContext:
        return await self._reader.get_pr_context(repo_full_name, number)


class InstallationRef:
    """The installation identity needed to mint GitHub clients."""

    def __init__(self, installation_id: int) -> None:
        self.installation_id = installation_id


class InstallationClients:
    """A reader and publisher sharing one installation access token."""

    def __init__(self, reader: ContextReader, publisher: Publisher) -> None:
        self.reader = reader
        self.publisher = publisher


#: Builds the GitHub reader/publisher pair for one installation.
ClientFactory = Callable[[InstallationRef, TokenCache], Awaitable[InstallationClients]]


class ReviewContext:
    """Per-worker bundle of shared clients and injectable factories.

    ``token_cache`` persists installation tokens across jobs so a long-running
    worker does not re-mint on every target. ``client_factory`` is the seam
    tests override to supply fake GitHub clients.
    """

    def __init__(
        self,
        *,
        llm: LlmClient,
        token_cache: TokenCache | None = None,
        config: WorkerConfig | None = None,
        client_factory: ClientFactory | None = None,
    ) -> None:
        self.llm = llm
        self.token_cache = token_cache if token_cache is not None else TokenCache()
        self.config = config if config is not None else WorkerConfig()
        self._client_factory: ClientFactory = client_factory or build_installation_clients

    async def build_clients(self, installation: InstallationRef) -> InstallationClients:
        """Build the GitHub reader/publisher pair for ``installation``."""
        return await self._client_factory(installation, self.token_cache)

    def build_harness(self, reader: ContextReader) -> ReviewHarness:
        """Build the single-pass harness around ``reader`` and the shared LLM."""
        return ReviewHarness(self.llm, _ReaderAdapter(reader))


def build_llm_client() -> LiteLlmClient:
    """Build the production LiteLLM client from process settings."""
    return LiteLlmClient.from_settings()


async def build_github_client(
    installation: InstallationRef, cache: TokenCache
) -> GitHubClient:
    """Mint (or reuse) a token and build a GitHub read client."""
    token = await _installation_token(installation, cache)
    return GitHubClient.from_installation_token(token)


async def build_publisher(
    installation: InstallationRef, cache: TokenCache
) -> GitHubPublisher:
    """Mint (or reuse) a token and build a GitHub publisher."""
    token = await _installation_token(installation, cache)
    return GitHubPublisher(GitHub(TokenAuthStrategy(token)))


async def build_installation_clients(
    installation: InstallationRef, cache: TokenCache
) -> InstallationClients:
    """Build both clients from one minted (or cached) installation token."""
    token = await _installation_token(installation, cache)
    return InstallationClients(
        reader=GitHubClient.from_installation_token(token),
        publisher=GitHubPublisher(GitHub(TokenAuthStrategy(token))),
    )


async def _installation_token(installation: InstallationRef, cache: TokenCache) -> str:
    """Return a fresh installation token, minting and caching it when needed."""
    cached = cache.get(installation.installation_id)
    if cached is not None:
        return cached

    settings = get_settings()
    app_id = settings.github_app_id
    private_key = settings.github_app_private_key
    if not app_id or not private_key:
        raise GitHubAuthError("GITHUB_APP_ID and GITHUB_APP_PRIVATE_KEY are required")

    app_client = GitHub(githubkit.auth.AppAuthStrategy(int(app_id), private_key))
    response = await app_client.rest.apps.async_create_installation_access_token(
        installation.installation_id
    )
    raise_for_status(response, "create installation token")
    minted = response.parsed_data
    cache.store(installation.installation_id, minted.token, parse_expiry(minted.expires_at))
    return minted.token


def db_session_factory() -> SessionFactory:
    """Return the process-wide database session factory."""
    return get_session
