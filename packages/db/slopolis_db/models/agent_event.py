"""Agent event model — the append-only run event log (spec §7/§8).

Every harness event (spawn, step, tool call/result, message, finding,
completion, failure) is one immutable row keyed by ``(run_id, seq)``. The seq
counter is monotonic per run, so the unique constraint both enforces ordering
integrity and gives the replay endpoint a stable cursor. Named ``AgentEventRow``
to avoid colliding with the core ``slopolis_core.harness.AgentEvent`` type.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from slopolis_db.base import Base, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from slopolis_db.models.agent_run import AgentRun


class AgentEventRow(UUIDPrimaryKeyMixin, Base):
    """One persisted harness event; ``(run_id, seq)`` is unique per run."""

    __tablename__ = "agent_events"
    __table_args__ = (UniqueConstraint("run_id", "seq"),)

    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    type: Mapped[str] = mapped_column(String(50), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    run: Mapped[AgentRun] = relationship()
