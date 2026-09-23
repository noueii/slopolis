"""the trigger access bar is derived, not stored

Revision ID: 0009_drop_required_access
Revises: 0008_target_reviewed_sha
Create Date: 2026-09-23 10:00:00.000000

Spec 10.2 states the bar a triggering user clears as one rule of the
repository's visibility — private repos need read, public repos need write — so
the per-repository override 0007 added has nothing left to express. The column
is dropped: pre-flight derives the bar from the row's own ``private`` flag at
the moment it checks access, and nothing about it is configurable or stored.

``downgrade`` restores the column on the rule as its default, which is what
every row held once no override could be set.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_drop_required_access"
down_revision: str | None = "0008_target_reviewed_sha"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("repositories") as batch_op:
        batch_op.drop_column("required_access")


def downgrade() -> None:
    with op.batch_alter_table("repositories") as batch_op:
        batch_op.add_column(
            sa.Column(
                "required_access",
                sa.String(length=20),
                nullable=False,
                server_default="default",
            )
        )
