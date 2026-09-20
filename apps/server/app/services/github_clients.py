"""Per-installation GitHub clients for the API server (spec 10.1).

The App may be installed on several accounts at once, and a workspace gains an
installation while the process runs, so the server cannot pick one installation
at boot. This module owns both halves of reading through the right one:

* :class:`InstallationClients` is built at startup from the App credentials and
  mints a **fresh** :class:`~slopolis_core.github.client.GitHubClient` for any
  installation id over one shared
  :class:`~slopolis_core.github.auth.TokenCache` — the minted installation token
  outlives the request, the client's read cache and tool budget do not.
* :class:`WorkspaceRepositories` turns a workspace's repository into the client
  for **its own** installation, per request: the repository list reads each
  repository through the installation that grants it, and a pre-flight link is
  resolved through its repository's installation. A repository the workspace has
  no row for, or whose installation cannot mint a token, resolves to ``None`` —
  the caller decides whether that is an absent count, a 503, or an installation
  to skip.

Nothing here talks to GitHub at construction time and nothing is cached across
requests, so an installation (or a repository) recorded after boot is visible to
the very next request.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from slopolis_core.github.auth import TokenCache
from slopolis_core.github.client import GitHubClient
from slopolis_db.models import GitHubInstallation, Repository

__all__ = [
    "InstallationClientSource",
    "InstallationClients",
    "WorkspaceRepositories",
    "WorkspaceRepository",
]

_logger = logging.getLogger(__name__)


class InstallationClientSource(Protocol):
    """The minting surface a workspace resolves its repositories through.

    :class:`InstallationClients` implements it in production; tests substitute a
    fake that answers with a client per installation id.
    """

    async def client_for(self, installation_id: int) -> GitHubClient: ...


class InstallationClients:
    """Mints one GitHub client per installation over a shared token cache."""

    def __init__(
        self,
        app_id: int,
        private_key: str,
        *,
        token_cache: TokenCache | None = None,
    ) -> None:
        self._app_id = app_id
        self._private_key = private_key
        self._tokens = token_cache if token_cache is not None else TokenCache()

    async def client_for(self, installation_id: int) -> GitHubClient:
        """Build a fresh client reading through ``installation_id``.

        Fresh on purpose: the read cache and the tool budget are per-job memos,
        so one long-lived client would answer a listing out of another request's
        memo and spend the review budget across requests. The installation token
        itself is cached, so the mint stays off the hot path until it nears
        expiry.
        """
        return await GitHubClient.from_app(
            self._app_id,
            self._private_key,
            installation_id=installation_id,
            cache=self._tokens,
        )


@dataclass(frozen=True, slots=True)
class WorkspaceRepository:
    """One of the workspace's repository rows with a client for its installation.

    ``client`` is ``None`` when the row's installation cannot be read — the
    repository is still listed, but nothing live can be answered for it.
    """

    row: Repository
    client: GitHubClient | None


class WorkspaceRepositories:
    """Resolves a workspace's repositories to clients for their installations.

    One instance per request. A repository row is read at most once and a client
    is minted at most once per installation, so listing every repository of an
    installation mints one token for it rather than one per row. Nothing is
    cached beyond the request, so a row or installation recorded mid-process is
    picked up by the next one.
    """

    def __init__(
        self,
        db: AsyncSession,
        workspace_id: uuid.UUID,
        source: InstallationClientSource | None,
    ) -> None:
        self._db = db
        self._workspace_id = workspace_id
        self._source = source
        self._repositories: dict[str, WorkspaceRepository | None] = {}
        self._clients: dict[uuid.UUID, GitHubClient | None] = {}

    async def resolve(self, full_name: str) -> WorkspaceRepository | None:
        """Return the workspace's row for ``full_name`` with its installation's client.

        ``None`` means the workspace has no such repository; a resolved row may
        still carry ``client=None`` when its installation cannot be read.
        """
        if full_name in self._repositories:
            return self._repositories[full_name]
        row = await self._db.scalar(
            select(Repository)
            .where(
                Repository.workspace_id == self._workspace_id,
                Repository.full_name == full_name,
            )
            .options(selectinload(Repository.installation))
        )
        resolved = (
            WorkspaceRepository(
                row=row,
                client=await self.client_for_installation(row.installation),
            )
            if row is not None
            else None
        )
        self._repositories[full_name] = resolved
        return resolved

    async def client_for(self, full_name: str) -> GitHubClient | None:
        """Return the client for ``full_name``'s own installation, or ``None``."""
        resolved = await self.resolve(full_name)
        return resolved.client if resolved is not None else None

    async def client_for_installation(
        self, installation: GitHubInstallation | None
    ) -> GitHubClient | None:
        """Return the client for one installation row, minting once per request.

        ``None`` for an absent row, for a workspace with no App credentials, and
        for an installation whose token cannot be minted (a suspended install, a
        placeholder row for an account that was never synced, a rejected key).
        """
        if installation is None:
            return None
        if installation.id in self._clients:
            return self._clients[installation.id]
        client = await self._mint(installation)
        self._clients[installation.id] = client
        return client

    async def installation_clients(self) -> list[GitHubClient]:
        """Return a readable client for every installation of the workspace."""
        rows = (
            await self._db.scalars(
                select(GitHubInstallation)
                .where(GitHubInstallation.workspace_id == self._workspace_id)
                .order_by(GitHubInstallation.installation_id)
            )
        ).all()
        clients: list[GitHubClient] = []
        for row in rows:
            client = await self.client_for_installation(row)
            if client is not None:
                clients.append(client)
        return clients

    async def by_installation(
        self,
    ) -> list[tuple[GitHubInstallation, list[Repository]]]:
        """Group the workspace's repositories by the installation that grants them.

        Ordered by GitHub's installation id and, within a group, by repository
        name, so a caller can read every group concurrently and still serve a
        stable order.
        """
        pairs = (
            await self._db.execute(
                select(GitHubInstallation, Repository)
                .join(Repository, Repository.installation_id == GitHubInstallation.id)
                .where(Repository.workspace_id == self._workspace_id)
                .order_by(GitHubInstallation.installation_id, Repository.full_name)
            )
        ).all()
        installations: dict[int, GitHubInstallation] = {}
        grouped: dict[int, list[Repository]] = {}
        for installation, row in pairs:
            key = installation.installation_id
            installations[key] = installation
            grouped.setdefault(key, []).append(row)
        return [(installations[key], grouped[key]) for key in sorted(grouped)]

    async def _mint(self, installation: GitHubInstallation) -> GitHubClient | None:
        """Mint a client for ``installation``, or ``None`` when that is impossible."""
        if self._source is None:
            return None
        try:
            return await self._source.client_for(installation.installation_id)
        except Exception as exc:
            # Degrade the reads this installation owns instead of failing the
            # request: the boot-time client this replaces was simply absent in
            # exactly these cases, and the caller's fallback is an absent count.
            _logger.warning(
                "GitHub client unavailable for installation %s: %s",
                installation.installation_id,
                exc,
            )
            return None
