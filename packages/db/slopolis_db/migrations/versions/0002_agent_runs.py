"""agent runs and events

Revision ID: 0002_agent_runs
Revises: 0001_initial
Create Date: 2026-09-16 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_agent_runs"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_runs",
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("target_id", sa.Uuid(), nullable=True),
        sa.Column("parent_run_id", sa.Uuid(), nullable=True),
        sa.Column("level", sa.String(length=50), nullable=False),
        sa.Column("role", sa.String(length=100), nullable=False),
        sa.Column("model_id", sa.String(length=255), nullable=True),
        sa.Column("objective", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("tokens", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Numeric(precision=12, scale=6), nullable=False),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["review_sessions.id"],
            name=op.f("fk_agent_runs_session_id_review_sessions"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["target_id"],
            ["session_targets.id"],
            name=op.f("fk_agent_runs_target_id_session_targets"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["parent_run_id"],
            ["agent_runs.id"],
            name=op.f("fk_agent_runs_parent_run_id_agent_runs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_runs")),
    )
    with op.batch_alter_table("agent_runs", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_agent_runs_session_id"), ["session_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_agent_runs_target_id"), ["target_id"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_agent_runs_parent_run_id"),
            ["parent_run_id"],
            unique=False,
        )

    op.create_table(
        "agent_events",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(length=50), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["agent_runs.id"],
            name=op.f("fk_agent_events_run_id_agent_runs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_events")),
        sa.UniqueConstraint("run_id", "seq", name=op.f("uq_agent_events_run_id")),
    )
    with op.batch_alter_table("agent_events", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_agent_events_run_id"), ["run_id"], unique=False)

    with op.batch_alter_table("findings", schema=None) as batch_op:
        batch_op.add_column(sa.Column("agent_run_id", sa.Uuid(), nullable=True))
        batch_op.create_foreign_key(
            batch_op.f("fk_findings_agent_run_id_agent_runs"),
            "agent_runs",
            ["agent_run_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_index(
            batch_op.f("ix_findings_agent_run_id"),
            ["agent_run_id"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("findings", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_findings_agent_run_id"))
        batch_op.drop_constraint(
            batch_op.f("fk_findings_agent_run_id_agent_runs"),
            type_="foreignkey",
        )
        batch_op.drop_column("agent_run_id")

    with op.batch_alter_table("agent_events", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_agent_events_run_id"))
    op.drop_table("agent_events")

    with op.batch_alter_table("agent_runs", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_agent_runs_parent_run_id"))
        batch_op.drop_index(batch_op.f("ix_agent_runs_target_id"))
        batch_op.drop_index(batch_op.f("ix_agent_runs_session_id"))
    op.drop_table("agent_runs")
