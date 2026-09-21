"""Webhook row writes (spec 10.1 §Webhooks).

``POST /api/github/webhook`` is the only write path GitHub starts: the setup
callback records an installation once, and these functions keep it current
afterwards, so the app stops depending on the user revisiting the install page.

Everything here takes a session and the *decoded payload* — no request or
response types — so a delivery is replayed in tests without HTTP. Every write is
scoped by the installation row's workspace; the one way a delivery introduces a
row is an ``installation`` event this deployment can place, which in v1
(single-tenant) is the deployment's one workspace.

Identity is GitHub's, not the name's: installations are matched on
``installation_id`` and repositories on their numeric ``github_id``, so a rename
updates the row it already has instead of creating a second one. Applying the
same delivery twice therefore leaves the same rows.
"""

from __future__ import annotations

import hashlib
import hmac
import uuid
from collections.abc import Mapping, Sequence
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from slopolis_db.models import GitHubInstallation, Repository, Workspace

__all__ = [
    "signature_matches",
    "sync_installation_event",
    "sync_installation_repositories_event",
    "sync_repository_event",
]

#: What GitHub prefixes the SHA-256 digest with in ``X-Hub-Signature-256``.
_SIGNATURE_PREFIX = "sha256="

#: ``installation`` actions that mean the App can mint a token again.
_REACHABLE_ACTIONS = frozenset({"created", "unsuspend"})

#: ``installation`` actions that mean it cannot, for now or ever.
_UNREACHABLE_ACTIONS = frozenset({"deleted", "suspend"})

#: ``repository`` actions this module follows.
_REPOSITORY_ACTIONS = frozenset({"renamed", "transferred", "deleted"})

#: Stored account type when a payload omits it (an enterprise account, say).
_UNKNOWN_ACCOUNT_TYPE = "Unknown"

#: Branch stored for a repository a payload describes without one.
_DEFAULT_BRANCH_FALLBACK = "main"


def signature_matches(*, secret: str | None, body: bytes, signature: str | None) -> bool:
    """Does ``signature`` authenticate exactly ``body`` under ``secret``?

    GitHub sends ``sha256=<hex digest>`` over the raw body. An unset secret, a
    missing header, or a digest that does not agree compares as ``False`` — the
    caller answers 401 and parses nothing. ``hmac.compare_digest`` keeps the
    comparison constant-time. The SHA-1 ``X-Hub-Signature`` header is not
    accepted: it is the legacy digest GitHub itself recommends against.
    """
    if not secret or not signature or not signature.startswith(_SIGNATURE_PREFIX):
        return False
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, signature[len(_SIGNATURE_PREFIX) :])


async def sync_installation_event(db: AsyncSession, payload: Mapping[str, Any]) -> bool:
    """Apply an ``installation`` delivery; return whether anything was written.

    ``created``/``unsuspend`` upsert the installation row (id, account login and
    type) and reconnect its repositories — the undo of a suspension, which is
    otherwise the state that makes every repository unusable. ``deleted`` and
    ``suspend`` mark the installation's repositories unusable; the rows stay, as
    history, because sessions and findings point at them (spec 10.1 §Repository
    selection).

    A payload this deployment cannot place — no row for that installation, and no
    single workspace to attribute a new one to — is ignored rather than guessed
    at, so an unknown installation never becomes a 500.
    """
    action = _str_field(payload, "action")
    remote = _mapping_field(payload, "installation")
    installation_id = _int_field(remote, "id")
    account = _mapping_field(remote, "account")
    login = _str_field(account, "login")
    if installation_id is None or login is None or action is None:
        return False

    installation = await _installation_by_id(db, installation_id)
    if action in _REACHABLE_ACTIONS:
        if installation is None:
            workspace_id = await _sole_workspace_id(db)
            if workspace_id is None:
                return False
            installation = GitHubInstallation(
                workspace_id=workspace_id,
                installation_id=installation_id,
                account_login=login,
                account_type=_UNKNOWN_ACCOUNT_TYPE,
            )
            db.add(installation)
        installation.installation_id = installation_id
        installation.account_login = login
        installation.account_type = (
            _str_field(account, "type") or installation.account_type
        )
        await db.flush()
        for row in await _repositories_of(db, installation):
            row.connected = True
        await db.flush()
        return True

    if action in _UNREACHABLE_ACTIONS:
        if installation is None:
            return False
        for row in await _repositories_of(db, installation):
            row.connected = False
        await db.flush()
        return True

    return False


async def sync_installation_repositories_event(
    db: AsyncSession, payload: Mapping[str, Any]
) -> bool:
    """Apply an ``installation_repositories`` delivery; ``True`` when it wrote.

    Both lists are applied — added repositories are upserted and reconnected,
    removed ones are marked unusable — and the ``action`` only labels the
    delivery, so an action GitHub adds later still ends in the right rows.

    A payload naming an installation this deployment does not know is ignored:
    only an ``installation`` event places one, and a repository delivery for an id
    no row carries has no workspace to write into.
    """
    installation = await _installation_of(db, payload)
    if installation is None:
        return False
    wrote = False
    for item in _sequence_field(payload, "repositories_added"):
        wrote = await _upsert_repository(db, installation, item, connected=True) or wrote
    for item in _sequence_field(payload, "repositories_removed"):
        wrote = await _disconnect_repository(db, installation, item) or wrote
    await db.flush()
    return wrote


async def sync_repository_event(db: AsyncSession, payload: Mapping[str, Any]) -> bool:
    """Apply a ``repository`` delivery; return whether anything was written.

    ``renamed``/``transferred`` follow the repository's ``github_id`` to the row
    it already has and update ``full_name``, ``private`` and the default branch.
    A transfer also re-points the row at the installation the payload names, when
    this deployment knows that installation and it belongs to the same workspace
    — otherwise the row would keep minting the previous account's token.
    ``deleted`` marks the row unusable instead of removing it, for the same
    history reason as a suspended installation.

    A repository the workspace has never recorded is added when the payload also
    names an installation we know (GitHub is telling us the App has it), and
    ignored when it does not — a delivery for an unknown repository must not
    invent one.
    """
    action = _str_field(payload, "action")
    item = _mapping_field(payload, "repository")
    github_id = _int_field(item, "id")
    full_name = _str_field(item, "full_name")
    if action not in _REPOSITORY_ACTIONS or github_id is None or full_name is None:
        return False

    installation = await _installation_of(db, payload)
    row = await _match_repository(
        db,
        github_id=github_id,
        full_names=_names_before_and_after(payload, full_name),
        workspace_id=installation.workspace_id if installation is not None else None,
    )
    if row is None:
        if action == "deleted" or installation is None:
            return False
        return await _upsert_repository(db, installation, item, connected=True)

    if action == "deleted":
        row.connected = False
        await db.flush()
        return True

    _apply_repository_fields(row, installation, item, github_id=github_id, full_name=full_name)
    await db.flush()
    return True


async def _upsert_repository(
    db: AsyncSession,
    installation: GitHubInstallation,
    item: Mapping[str, Any],
    *,
    connected: bool,
) -> bool:
    """Write one payload item, matched on its numeric id then on its name."""
    github_id = _int_field(item, "id")
    full_name = _str_field(item, "full_name")
    if github_id is None or full_name is None:
        return False
    row = await _match_repository(
        db,
        github_id=github_id,
        full_names=[full_name],
        workspace_id=installation.workspace_id,
    )
    if row is None:
        db.add(
            Repository(
                workspace_id=installation.workspace_id,
                installation_id=installation.id,
                github_id=github_id,
                full_name=full_name,
                private=_bool_field(item, "private") or False,
                default_branch=(
                    _str_field(item, "default_branch") or _DEFAULT_BRANCH_FALLBACK
                ),
                connected=connected,
            )
        )
        await db.flush()
        return True
    _apply_repository_fields(row, installation, item, github_id=github_id, full_name=full_name)
    row.connected = connected
    await db.flush()
    return True


async def _disconnect_repository(
    db: AsyncSession, installation: GitHubInstallation, item: Mapping[str, Any]
) -> bool:
    """Mark one repository of ``installation`` unusable, if the workspace has it."""
    full_name = _str_field(item, "full_name")
    row = await _match_repository(
        db,
        github_id=_int_field(item, "id"),
        full_names=[full_name] if full_name else [],
        workspace_id=installation.workspace_id,
    )
    if row is None:
        return False
    row.connected = False
    return True


def _apply_repository_fields(
    row: Repository,
    installation: GitHubInstallation | None,
    item: Mapping[str, Any],
    *,
    github_id: int,
    full_name: str,
) -> None:
    """Copy a payload's repository fields onto ``row``.

    A row the app created from a PR link carries ``github_id == 0``: a payload
    naming it is the moment to learn the real id, which is what a later rename is
    followed by.
    """
    row.full_name = full_name
    if not row.github_id:
        row.github_id = github_id
    private = _bool_field(item, "private")
    if private is not None:
        row.private = private
    branch = _str_field(item, "default_branch")
    if branch:
        row.default_branch = branch
    if installation is not None and installation.workspace_id == row.workspace_id:
        row.installation_id = installation.id


async def _match_repository(
    db: AsyncSession,
    *,
    github_id: int | None,
    full_names: Sequence[str],
    workspace_id: uuid.UUID | None,
) -> Repository | None:
    """Find one repository by GitHub's numeric id, then by any name it is known by.

    Scoped to ``workspace_id`` when the delivery names an installation we know;
    a delivery that names none (a transfer, which arrives for the new owner's
    installation) still matches the row by id, because that id is the identity
    the workspace recorded.
    """
    criteria = [Repository.full_name == name for name in full_names]
    if github_id is not None:
        criteria.insert(0, Repository.github_id == github_id)
    for criterion in criteria:
        statement = select(Repository).where(criterion)
        if workspace_id is not None:
            statement = statement.where(Repository.workspace_id == workspace_id)
        row = await db.scalar(statement)
        if row is not None:
            return row
    return None


async def _installation_by_id(
    db: AsyncSession, installation_id: int
) -> GitHubInstallation | None:
    """The workspace's row for one GitHub installation, if it has one."""
    return await db.scalar(
        select(GitHubInstallation).where(
            GitHubInstallation.installation_id == installation_id
        )
    )


async def _installation_of(
    db: AsyncSession, payload: Mapping[str, Any]
) -> GitHubInstallation | None:
    """The row named by a payload's ``installation`` object, if the workspace has one."""
    installation_id = _int_field(_mapping_field(payload, "installation"), "id")
    if installation_id is None:
        return None
    return await _installation_by_id(db, installation_id)


async def _repositories_of(
    db: AsyncSession, installation: GitHubInstallation
) -> Sequence[Repository]:
    """Every repository row the installation owns."""
    return (
        await db.scalars(
            select(Repository).where(Repository.installation_id == installation.id)
        )
    ).all()


async def _sole_workspace_id(db: AsyncSession) -> uuid.UUID | None:
    """The deployment's workspace, when it has exactly one.

    v1 is single-tenant, so a delivery that introduces an installation has one
    plausible home. In a deployment with several workspaces — or none — there is
    nothing to attribute it to, and the delivery is ignored rather than guessed.
    """
    workspace_ids = (await db.scalars(select(Workspace.id).limit(2))).all()
    return workspace_ids[0] if len(workspace_ids) == 1 else None


def _names_before_and_after(payload: Mapping[str, Any], full_name: str) -> list[str]:
    """``full_name`` plus the name the payload says the repository had before.

    A rename keeps its owner and reports the old short name; a transfer reports
    the old owner. Both are only used as a fallback for rows recorded before
    their numeric id was known.
    """
    owner, _, name = full_name.partition("/")
    changes = _mapping_field(payload, "changes")
    repository_changes = _mapping_field(changes, "repository")
    renamed_from = _text_field(_mapping_field(repository_changes, "name"), "from")
    owner_from = _text_field(_mapping_field(changes, "owner"), "from")
    names = [full_name]
    if renamed_from:
        names.append(f"{owner}/{renamed_from}")
    if owner_from:
        names.append(f"{owner_from}/{name}")
    return names


def _text_field(source: Mapping[str, Any], key: str) -> str | None:
    """A string field, or the ``login`` of an account object under it.

    GitHub writes the previous value of a changed field sometimes plainly and
    sometimes as the full account object, so both shapes are read.
    """
    value = source.get(key)
    if isinstance(value, str):
        return value or None
    if not isinstance(value, dict):
        return None
    login = cast("Mapping[str, Any]", value).get("login")
    return login if isinstance(login, str) and login else None


def _mapping_field(source: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    """A payload object field, or an empty mapping when it is absent or not one."""
    value = source.get(key)
    if not isinstance(value, dict):
        return {}
    return cast("Mapping[str, Any]", value)


def _sequence_field(
    source: Mapping[str, Any], key: str
) -> Sequence[Mapping[str, Any]]:
    """A payload array field's object items, ignoring anything else in it."""
    value = source.get(key)
    if not isinstance(value, list):
        return []
    return [
        cast("Mapping[str, Any]", item)
        for item in cast("list[Any]", value)
        if isinstance(item, dict)
    ]


def _str_field(source: Mapping[str, Any], key: str) -> str | None:
    """A non-empty string field, or ``None``."""
    value = source.get(key)
    return value if isinstance(value, str) and value else None


def _int_field(source: Mapping[str, Any], key: str) -> int | None:
    """An integer field, or ``None``."""
    value = source.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _bool_field(source: Mapping[str, Any], key: str) -> bool | None:
    """A boolean field, or ``None``."""
    value = source.get(key)
    return value if isinstance(value, bool) else None
