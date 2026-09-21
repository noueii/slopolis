"""workspace caps and per-repository access policy

Revision ID: 0007_workspace_limits
Revises: 0006_repository_enabled
Create Date: 2026-09-20 19:20:00.000000

Spec 10.10 adds the opt-in caps the workspace applies before it spawns work:
four nullable integers that are ``NULL`` when unset (unlimited), so every
existing deployment keeps behaving exactly as it did. ``repositories`` gains
the per-repository access override alongside its enable switch, defaulting to
``default`` — the spec rule — so every existing row keeps the access rule it
already had.

The ``required_access`` server default stays in place after the backfill so a
row inserted by anything that does not go through the ORM applies the spec rule
too.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_workspace_limits"
down_revision: str | None = "0006_repository_enabled"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: The workspace's caps (spec 10.10), all nullable so unset means unlimited.
_CAP_COLUMNS = (
    "max_concurrent_sessions",
    "max_sessions_per_user_per_day",
    "max_targets_per_repo",
    "max_targets_per_installation",
)


def upgrade() -> None:
    with op.batch_alter_table("workspaces") as batch_op:
        for name in _CAP_COLUMNS:
            batch_op.add_column(sa.Column(name, sa.Integer(), nullable=True))

    with op.batch_alter_table("repositories") as batch_op:
        batch_op.add_column(
            sa.Column(
                "required_access",
                sa.String(length=20),
                nullable=False,
                server_default="default",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("repositories") as batch_op:
        batch_op.drop_column("required_access")

    with op.batch_alter_table("workspaces") as batch_op:
        for name in reversed(_CAP_COLUMNS):
            batch_op.drop_column(name)
