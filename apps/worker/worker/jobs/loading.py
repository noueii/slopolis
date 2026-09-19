"""Loading the database graph for one session target (spec 10.5).

Reads the session, target, repository, installation, and workspace state the
job needs in one place, and exposes the terminal-status guards the job checks
before doing any work. Unknown ids and already-terminal targets are reported as
typed outcomes rather than exceptions.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import StrEnum, auto

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from slopolis_core.domain import SessionStatus, TargetStatus
from slopolis_db.models import (
    GitHubInstallation,
    Repository,
    ReviewSession,
    SessionTarget,
    Workspace,
)

__all__ = ["LoadOutcome", "LoadResult", "TargetJob", "load_target"]

_TERMINAL_TARGETS = frozenset(
    {TargetStatus.DONE, TargetStatus.FAILED, TargetStatus.CANCELLED, TargetStatus.SKIPPED}
)
_TERMINAL_SESSION = frozenset({SessionStatus.DONE, SessionStatus.FAILED, SessionStatus.CANCELLED})


class LoadOutcome(StrEnum):
    """Why a target was (not) loaded for execution."""

    READY = auto()
    MISSING = auto()
    TARGET_TERMINAL = auto()
    SESSION_CANCELLED = auto()
    SESSION_TERMINAL = auto()


@dataclass(frozen=True, slots=True)
class TargetJob:
    """Everything the job reads before running one target."""

    session: ReviewSession
    target: SessionTarget
    repository: Repository
    installation: GitHubInstallation
    workspace: Workspace

    @property
    def repo_full_name(self) -> str:
        return self.repository.full_name

    @property
    def session_id(self) -> uuid.UUID:
        return self.session.id

    @property
    def target_id(self) -> uuid.UUID:
        return self.target.id


@dataclass(frozen=True, slots=True)
class LoadResult:
    """The load outcome plus the job graph when the target is ready."""

    outcome: LoadOutcome
    job: TargetJob | None = None


async def load_target(db: AsyncSession, session_id: str, target_id: str) -> LoadResult:
    """Load the target graph and classify whether it should run.

    A cancelled session is reported as :attr:`LoadOutcome.SESSION_CANCELLED` so
    the caller can mark the target cancelled; a terminal target or session is
    skipped with no side effects. A missing/mismatched id is
    :attr:`LoadOutcome.MISSING`.
    """
    session_uuid, target_uuid = _parse_ids(session_id, target_id)
    if session_uuid is None or target_uuid is None:
        return LoadResult(outcome=LoadOutcome.MISSING)

    result = await db.execute(
        select(SessionTarget)
        .where(SessionTarget.id == target_uuid, SessionTarget.session_id == session_uuid)
        .options(
            selectinload(SessionTarget.repository).selectinload(Repository.installation),
            selectinload(SessionTarget.session).selectinload(ReviewSession.workspace),
        )
    )
    target = result.scalar_one_or_none()
    if target is None:
        return LoadResult(outcome=LoadOutcome.MISSING)

    session = target.session
    workspace = session.workspace
    repository = target.repository
    job = TargetJob(
        session=session,
        target=target,
        repository=repository,
        installation=repository.installation,
        workspace=workspace,
    )

    if _is_terminal_target(target.status):
        return LoadResult(outcome=LoadOutcome.TARGET_TERMINAL, job=job)
    if session.status == SessionStatus.CANCELLED:
        return LoadResult(outcome=LoadOutcome.SESSION_CANCELLED, job=job)
    if _is_terminal_session(session.status):
        return LoadResult(outcome=LoadOutcome.SESSION_TERMINAL, job=job)

    return LoadResult(outcome=LoadOutcome.READY, job=job)


def _parse_ids(session_id: str, target_id: str) -> tuple[uuid.UUID | None, uuid.UUID | None]:
    """Parse the two string ids, returning ``(None, None)`` on malformed input."""
    try:
        return uuid.UUID(session_id), uuid.UUID(target_id)
    except ValueError:
        return None, None


def _is_terminal_target(status: str) -> bool:
    return status in _TERMINAL_TARGETS


def _is_terminal_session(status: str) -> bool:
    return status in _TERMINAL_SESSION
