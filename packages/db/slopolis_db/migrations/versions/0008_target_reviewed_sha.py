"""session targets remember the commit their review covered

Revision ID: 0008_target_reviewed_sha
Revises: 0007_workspace_limits
Create Date: 2026-09-21 09:10:00.000000

Spec v3 §2 makes the pull-request inbox compare a review against the code it
read: a review is only worth calling current when the app knows which commit it
covered, and telling a current review from a stale one is the column that makes
the inbox worth opening. ``reviewed_sha`` therefore records the head commit a
review attempt ran against, which is what the attempt's findings came from —
not the commit the pull request happens to sit at now.

The column is nullable and stays that way: every target created before this
revision was reviewed without the app recording a commit, and a target that has
never completed a review has none either. NULL means "unknown", never "current":
the inbox reports such a target as reviewed but not tracked to a commit, and
never as a review with a known distance behind the pull request.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_target_reviewed_sha"
down_revision: str | None = "0007_workspace_limits"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("session_targets") as batch_op:
        batch_op.add_column(sa.Column("reviewed_sha", sa.String(length=64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("session_targets") as batch_op:
        batch_op.drop_column("reviewed_sha")
