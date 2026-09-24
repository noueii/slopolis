"""Review session, per-PR target, and target run models."""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from slopolis_db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from slopolis_db.models.finding import Finding
    from slopolis_db.models.github import Repository
    from slopolis_db.models.workspace import Workspace


class ReviewSession(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One review submission: a prompt plus N PR targets."""

    __tablename__ = "review_sessions"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    prompt: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="queued")
    model: Mapped[str] = mapped_column(String(255), nullable=False)
    provider: Mapped[str] = mapped_column(String(100), nullable=False)
    triggered_by_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    workspace: Mapped[Workspace] = relationship(back_populates="review_sessions")
    targets: Mapped[list[SessionTarget]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
    )


class SessionTarget(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A single pull request within a review session."""

    __tablename__ = "session_targets"
    __table_args__ = (UniqueConstraint("session_id", "repository_id", "number"),)

    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("review_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    repository_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    head_branch: Mapped[str] = mapped_column(String(255), nullable=False)
    #: Head SHA the last review attempt covered (spec v3 §2). Null until an
    #: attempt reviews the pull request; the inbox compares it against the pull
    #: request's current head to say how far behind a review is.
    reviewed_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="queued")
    tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False, default=Decimal("0"))
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    session: Mapped[ReviewSession] = relationship(back_populates="targets")
    repository: Mapped[Repository] = relationship()
    runs: Mapped[list[SessionTargetRun]] = relationship(
        back_populates="target",
        cascade="all, delete-orphan",
    )
    findings: Mapped[list[Finding]] = relationship(
        back_populates="target",
        cascade="all, delete-orphan",
    )


class SessionTargetRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One execution attempt for a session target."""

    __tablename__ = "session_target_runs"

    target_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("session_targets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="queued")
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False, default=Decimal("0"))
    error: Mapped[str | None] = mapped_column(String, nullable=True)

    target: Mapped[SessionTarget] = relationship(back_populates="runs")
