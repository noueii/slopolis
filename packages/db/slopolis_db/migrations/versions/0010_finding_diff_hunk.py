"""findings keep the hunk their comment is anchored to

Revision ID: 0010_finding_diff_hunk
Revises: 0009_drop_required_access
Create Date: 2026-09-24 09:00:00.000000

Spec 10.7 shows a finding's comment the way GitHub shows it: the code hunk the
comment sits on, rendered above the comment. GitHub returns that text with every
review comment it creates or lists (``diff_hunk``), and it is stored as GitHub
wrote it instead of being recomputed from the diff the review read — the two
differ in exactly the cases a reader would notice, so "exactly like GitHub" is
only true of GitHub's own string.

The column is nullable and stays that way. A finding with no comment has no hunk,
and neither does any comment posted before this revision: the app never kept the
hunk, and nothing it stored can reconstruct one.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_finding_diff_hunk"
down_revision: str | None = "0009_drop_required_access"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("findings") as batch_op:
        batch_op.add_column(sa.Column("diff_hunk", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("findings") as batch_op:
        batch_op.drop_column("diff_hunk")
