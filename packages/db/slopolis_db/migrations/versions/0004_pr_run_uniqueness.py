"""one PR orchestrator per (session, target)

Revision ID: 0004_pr_run_uniqueness
Revises: 0003_user_workspace_nullable
Create Date: 2026-09-20 16:20:00.000000

Harness V1.1 enforces the PR orchestrator's 1:1 (spec v2 §2, §15) in the database
rather than in the model: at most one ``level='pr'`` row per
``(session_id, target_id)``, so a retried attempt reuses its run row instead of
adding a second orchestrator for the same target. The index is partial because
``main`` and ``sub`` rows must stay unconstrained — a session has one main row,
and a target may legitimately have many sub-agent rows.

Spec §8 also lists an index on ``agent_event(run_id, seq)``; 0002 already created
``uq_agent_events_run_id`` — a unique constraint on exactly those columns — which
is that index, so this revision adds nothing redundant for it.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_pr_run_uniqueness"
down_revision: str | None = "0003_user_workspace_nullable"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("agent_runs", schema=None) as batch_op:
        batch_op.create_index(
            op.f("ix_agent_runs_pr_target"),
            ["session_id", "target_id"],
            unique=True,
            sqlite_where=sa.text("level = 'pr'"),
            postgresql_where=sa.text("level = 'pr'"),
        )


def downgrade() -> None:
    with op.batch_alter_table("agent_runs", schema=None) as batch_op:
        batch_op.drop_index(op.f("ix_agent_runs_pr_target"))
