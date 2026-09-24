"""Finding model — a structured review comment attached to a session target."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, Boolean, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from slopolis_db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from slopolis_db.models.agent_run import AgentRun
    from slopolis_db.models.review import SessionTarget, SessionTargetRun


class Finding(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A single review finding for a target, optionally tied to a run attempt."""

    __tablename__ = "findings"

    target_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("session_targets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("session_target_runs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    path: Mapped[str] = mapped_column(String(1024), nullable=False)
    line: Mapped[int | None] = mapped_column(Integer, nullable=True)
    severity: Mapped[str] = mapped_column(String(50), nullable=False)
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    message: Mapped[str] = mapped_column(String, nullable=False)
    suggestion: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    github_comment_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    #: GitHub's own hunk for the finding's review comment — the ``@@ … @@`` header
    #: and its ``+``/``-``/context lines, exactly the text GitHub renders above the
    #: comment. Kept as GitHub wrote it rather than recomputed from the diff the
    #: review read, because showing the comment "the way GitHub does" is only true
    #: of GitHub's own text. Null when the finding has no comment, which includes
    #: every comment posted before this column existed: nothing the app stored
    #: could reconstruct a hunk for those.
    diff_hunk: Mapped[str | None] = mapped_column(Text, nullable=True)
    posted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    target: Mapped[SessionTarget] = relationship(back_populates="findings")
    run: Mapped[SessionTargetRun | None] = relationship()
    agent_run: Mapped[AgentRun | None] = relationship()
