"""store the signed-in user's GitHub access token

Revision ID: 0005_user_github_token
Revises: 0004_pr_run_uniqueness
Create Date: 2026-09-20 18:40:00.000000

The OAuth callback keeps the user-to-server access token (spec 10.1): it is the
only credential that can answer "may this member read this repository?", which
the installation token cannot, because it sees every repository the App was
granted (10.8 §Access). The token is sealed with spec 10.2's ``SecretVault``, so
the column holds an opaque blob and never plaintext.

Both columns are nullable on purpose: a deployment without ``ENCRYPTION_KEY``
still signs users in and simply stores nothing, which makes every per-user
repo-access check unverifiable.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_user_github_token"
down_revision: str | None = "0004_pr_run_uniqueness"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(
            sa.Column("encrypted_github_token", sa.LargeBinary(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("token_updated_at", sa.DateTime(timezone=True), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("token_updated_at")
        batch_op.drop_column("encrypted_github_token")
