"""users can exist before they belong to a workspace

Revision ID: 0003_user_workspace_nullable
Revises: 0002_agent_runs
Create Date: 2026-09-20 15:10:00.000000

Sign-in no longer provisions a workspace: a new account is created with
``workspace_id = NULL`` and then either creates a workspace (becoming its admin)
or waits for an invitation. Downgrading therefore fails while any user is still
workspace-less, which is the honest outcome — attach or delete those accounts
first.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_user_workspace_nullable"
down_revision: str | None = "0002_agent_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.alter_column(
            "workspace_id",
            existing_type=sa.Uuid(),
            nullable=True,
        )


def downgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.alter_column(
            "workspace_id",
            existing_type=sa.Uuid(),
            nullable=False,
        )
