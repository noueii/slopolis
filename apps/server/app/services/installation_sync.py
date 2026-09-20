"""Installation and repository sync (spec 10.1).

GitHub's setup URL is where the server learns an installation id: the browser is
sent there after an install, or after a repository-selection change when
"Redirect on update" is on. Webhooks arrive in Phase 2, so this is also the only
path that records installations — the worker mints tokens from the rows this
module writes.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from slopolis_core.github.models import AppInstallation, InstallationRepository
from slopolis_db.models import GitHubInstallation, Repository

__all__ = [
    "InstallationClaimedError",
    "InstallationSource",
    "SyncOutcome",
    "sync_installation",
]


class InstallationClaimedError(Exception):
    """The installation is already recorded for a different workspace.

    v1 is single-tenant, so this means the deployment's data disagrees with
    GitHub rather than a normal multi-tenant conflict.
    """


class InstallationSource(Protocol):
    """The App-JWT surface the install flow needs.

    Core's ``AppInstallations`` implements it. Sync itself uses the first two
    methods; ``app_slug`` builds the install URL when ``GITHUB_APP_SLUG`` is not
    configured, which is why it belongs to the same narrow port.
    """

    async def app_slug(self) -> str: ...

    async def get_installation(self, installation_id: int) -> AppInstallation: ...

    async def list_repositories(
        self, installation_id: int
    ) -> Sequence[InstallationRepository]: ...


@dataclass(frozen=True, slots=True)
class SyncOutcome:
    """What one sync recorded, for logging and tests."""

    installation_id: int
    account_login: str
    repository_count: int
    created: bool
    suspended: bool


async def sync_installation(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    installation_id: int,
    source: InstallationSource,
) -> SyncOutcome:
    """Record one installation and its repositories for a workspace.

    Idempotent: a repeat call (an "update" redirect, a re-install) refreshes the
    account fields and repository set instead of duplicating rows.
    """
    remote = await source.get_installation(installation_id)
    installation = await db.scalar(
        select(GitHubInstallation).where(
            GitHubInstallation.installation_id == installation_id
        )
    )
    created = installation is None
    if installation is None:
        installation = GitHubInstallation(
            workspace_id=workspace_id,
            installation_id=installation_id,
            account_login=remote.account_login,
            account_type=remote.account_type,
        )
        db.add(installation)
        await db.flush()
    elif installation.workspace_id != workspace_id:
        raise InstallationClaimedError(
            f"Installation {installation_id} is already recorded for another workspace."
        )

    installation.account_login = remote.account_login
    installation.account_type = remote.account_type

    if remote.suspended:
        # A suspended installation cannot mint a token, so its repositories are
        # kept as history but marked unusable.
        await _disconnect_repositories(db, installation)
        await db.flush()
        return SyncOutcome(
            installation_id=installation_id,
            account_login=remote.account_login,
            repository_count=0,
            created=created,
            suspended=True,
        )

    remote_repositories = list(await source.list_repositories(installation_id))
    await _upsert_repositories(
        db,
        workspace_id=workspace_id,
        installation=installation,
        remote=remote_repositories,
    )
    await db.flush()
    return SyncOutcome(
        installation_id=installation_id,
        account_login=remote.account_login,
        repository_count=len(remote_repositories),
        created=created,
        suspended=False,
    )


async def _upsert_repositories(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    installation: GitHubInstallation,
    remote: Sequence[InstallationRepository],
) -> None:
    """Insert, update, and re-attach repositories; disconnect the ones that left.

    Rows are matched on ``full_name`` (unique) rather than within this
    installation, so a repository that moves between installations — a transfer,
    or a re-install that changes the account — updates in place instead of
    colliding.
    """
    existing = {
        row.full_name: row
        for row in (
            await db.scalars(
                select(Repository).where(Repository.workspace_id == workspace_id)
            )
        ).all()
    }
    seen: set[str] = set()
    for item in remote:
        seen.add(item.full_name)
        row = existing.get(item.full_name)
        if row is None:
            row = Repository(
                workspace_id=workspace_id,
                installation_id=installation.id,
                github_id=item.github_id,
                full_name=item.full_name,
                private=item.private,
                default_branch=item.default_branch,
            )
            db.add(row)
        row.installation_id = installation.id
        row.github_id = item.github_id
        row.private = item.private
        row.default_branch = item.default_branch
        row.connected = True
    await db.flush()

    for full_name, row in existing.items():
        if full_name not in seen and row.installation_id == installation.id:
            row.connected = False


async def _disconnect_repositories(
    db: AsyncSession, installation: GitHubInstallation
) -> None:
    """Mark every repository of a suspended installation as unusable."""
    rows = (
        await db.scalars(
            select(Repository).where(Repository.installation_id == installation.id)
        )
    ).all()
    for row in rows:
        row.connected = False
