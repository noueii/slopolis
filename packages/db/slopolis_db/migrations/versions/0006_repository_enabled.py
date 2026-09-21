"""repositories carry the workspace's own enable switch

Revision ID: 0006_repository_enabled
Revises: 0005_user_github_token
Create Date: 2026-09-20 18:40:00.000000

Spec 10.1 splits repository membership into two facts: ``connected`` is what the
installation still grants, ``enabled`` is whether this workspace still reviews
it. A parked repository stays listed with its history and is refused at
pre-flight, so every existing row is created enabled — the switch only ever
moves because someone moved it.

The server default stays in place after the backfill so a row inserted by
anything that does not go through the ORM is enabled too.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_repository_enabled"
down_revision: str | None = "0005_user_github_token"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("repositories") as batch_op:
        batch_op.add_column(
            sa.Column(
                "enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("repositories") as batch_op:
        batch_op.drop_column("enabled")
