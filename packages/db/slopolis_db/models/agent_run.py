"""Agent run model — one node of the persisted supervisor run tree (spec §5/§8).

A session fans out to one PR orchestrator per target, and each orchestrator may
spawn review sub-agents. Every node in that hierarchy is an ``AgentRun``:
``parent_run_id`` encodes the tree, ``target_id`` ties a PR-level run to its
target, and ``level``/``role`` mirror the core harness vocabulary. Columns are
kept as plain primitives so the db layer stays independent of
``slopolis_core.harness``.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Numeric, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from slopolis_db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from slopolis_db.models.review import ReviewSession, SessionTarget


class AgentRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One agent execution in the session → PR → sub-agent run tree."""

    __tablename__ = "agent_runs"
    #: The 1:1 rule of spec §2/§15 is enforced here, not by the model: at most one
    #: ``level='pr'`` row per (session, target), so a retried attempt reuses its row
    #: instead of adding a second PR orchestrator. The index is partial — a session
    #: has one ``main`` row and a target may have many ``sub`` rows. Created by
    #: migration 0004; the dialect filters keep the constraint live on SQLite.
    __table_args__ = (
        Index(
            "ix_agent_runs_pr_target",
            "session_id",
            "target_id",
            unique=True,
            postgresql_where=text("level = 'pr'"),
            sqlite_where=text("level = 'pr'"),
        ),
    )

    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("review_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    target_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("session_targets.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    parent_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    level: Mapped[str] = mapped_column(String(50), nullable=False)
    role: Mapped[str] = mapped_column(String(100), nullable=False)
    model_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    objective: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="pending")
    tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False, default=Decimal("0"))
    error: Mapped[str | None] = mapped_column(String, nullable=True)
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    session: Mapped[ReviewSession] = relationship()
    target: Mapped[SessionTarget | None] = relationship()
    parent: Mapped[AgentRun | None] = relationship(
        back_populates="children",
        remote_side="AgentRun.id",
    )
    children: Mapped[list[AgentRun]] = relationship(
        back_populates="parent",
        cascade="all, delete-orphan",
    )
