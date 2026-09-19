"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-09-13 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workspaces",
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("slug", sa.String(length=255), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workspaces")),
        sa.UniqueConstraint("slug", name=op.f("uq_workspaces_slug")),
    )

    op.create_table(
        "users",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("github_id", sa.BigInteger(), nullable=False),
        sa.Column("handle", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("avatar_url", sa.String(length=2048), nullable=True),
        sa.Column("is_admin", sa.Boolean(), nullable=False),
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
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_users_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("github_id", name=op.f("uq_users_github_id")),
    )
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_users_workspace_id"), ["workspace_id"], unique=False)

    op.create_table(
        "github_installations",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("installation_id", sa.BigInteger(), nullable=False),
        sa.Column("account_login", sa.String(length=255), nullable=False),
        sa.Column("account_type", sa.String(length=50), nullable=False),
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
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_github_installations_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_github_installations")),
        sa.UniqueConstraint(
            "installation_id",
            name=op.f("uq_github_installations_installation_id"),
        ),
    )
    with op.batch_alter_table("github_installations", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_github_installations_workspace_id"),
            ["workspace_id"],
            unique=False,
        )

    op.create_table(
        "repositories",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("installation_id", sa.Uuid(), nullable=False),
        sa.Column("github_id", sa.BigInteger(), nullable=False),
        sa.Column("full_name", sa.String(length=512), nullable=False),
        sa.Column("private", sa.Boolean(), nullable=False),
        sa.Column("default_branch", sa.String(length=255), nullable=False),
        sa.Column("connected", sa.Boolean(), nullable=False),
        sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=True),
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
            ["installation_id"],
            ["github_installations.id"],
            name=op.f("fk_repositories_installation_id_github_installations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_repositories_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_repositories")),
        sa.UniqueConstraint("full_name", name=op.f("uq_repositories_full_name")),
    )
    with op.batch_alter_table("repositories", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_repositories_installation_id"),
            ["installation_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_repositories_workspace_id"),
            ["workspace_id"],
            unique=False,
        )

    op.create_table(
        "provider_credentials",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=100), nullable=False),
        sa.Column("base_url", sa.String(length=2048), nullable=True),
        sa.Column("encrypted_api_key", sa.LargeBinary(), nullable=False),
        sa.Column("key_last4", sa.String(length=4), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("last_status", sa.String(length=50), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
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
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_provider_credentials_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_provider_credentials")),
    )
    with op.batch_alter_table("provider_credentials", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_provider_credentials_workspace_id"),
            ["workspace_id"],
            unique=False,
        )

    op.create_table(
        "model_catalog",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("credential_id", sa.Uuid(), nullable=True),
        sa.Column("model_id", sa.String(length=255), nullable=False),
        sa.Column("provider", sa.String(length=100), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=True),
        sa.Column("source", sa.String(length=50), nullable=False),
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
            ["credential_id"],
            ["provider_credentials.id"],
            name=op.f("fk_model_catalog_credential_id_provider_credentials"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_model_catalog_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_model_catalog")),
        sa.UniqueConstraint("workspace_id", "model_id", name=op.f("uq_model_catalog_workspace_id")),
    )
    with op.batch_alter_table("model_catalog", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_model_catalog_credential_id"),
            ["credential_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_model_catalog_workspace_id"),
            ["workspace_id"],
            unique=False,
        )

    op.create_table(
        "model_assignments",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=100), nullable=False),
        sa.Column("model_catalog_id", sa.Uuid(), nullable=True),
        sa.Column("model_id", sa.String(length=255), nullable=True),
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
            ["model_catalog_id"],
            ["model_catalog.id"],
            name=op.f("fk_model_assignments_model_catalog_id_model_catalog"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_model_assignments_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_model_assignments")),
        sa.UniqueConstraint(
            "workspace_id",
            "role",
            name=op.f("uq_model_assignments_workspace_id"),
        ),
    )
    with op.batch_alter_table("model_assignments", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_model_assignments_model_catalog_id"),
            ["model_catalog_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_model_assignments_workspace_id"),
            ["workspace_id"],
            unique=False,
        )

    op.create_table(
        "review_sessions",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("prompt", sa.String(), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("model", sa.String(length=255), nullable=False),
        sa.Column("provider", sa.String(length=100), nullable=False),
        sa.Column("triggered_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
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
            ["triggered_by_user_id"],
            ["users.id"],
            name=op.f("fk_review_sessions_triggered_by_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_review_sessions_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_review_sessions")),
    )
    with op.batch_alter_table("review_sessions", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_review_sessions_triggered_by_user_id"),
            ["triggered_by_user_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_review_sessions_workspace_id"),
            ["workspace_id"],
            unique=False,
        )

    op.create_table(
        "session_targets",
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("repository_id", sa.Uuid(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("url", sa.String(length=2048), nullable=False),
        sa.Column("head_branch", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("tokens", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Numeric(precision=12, scale=6), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
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
            ["repository_id"],
            ["repositories.id"],
            name=op.f("fk_session_targets_repository_id_repositories"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["review_sessions.id"],
            name=op.f("fk_session_targets_session_id_review_sessions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_session_targets")),
        sa.UniqueConstraint(
            "session_id",
            "repository_id",
            "number",
            name=op.f("uq_session_targets_session_id"),
        ),
    )
    with op.batch_alter_table("session_targets", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_session_targets_repository_id"),
            ["repository_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_session_targets_session_id"),
            ["session_id"],
            unique=False,
        )

    op.create_table(
        "session_target_runs",
        sa.Column("target_id", sa.Uuid(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tokens", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Numeric(precision=12, scale=6), nullable=False),
        sa.Column("error", sa.String(), nullable=True),
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
            ["target_id"],
            ["session_targets.id"],
            name=op.f("fk_session_target_runs_target_id_session_targets"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_session_target_runs")),
    )
    with op.batch_alter_table("session_target_runs", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_session_target_runs_target_id"),
            ["target_id"],
            unique=False,
        )

    op.create_table(
        "findings",
        sa.Column("target_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=True),
        sa.Column("path", sa.String(length=1024), nullable=False),
        sa.Column("line", sa.Integer(), nullable=True),
        sa.Column("severity", sa.String(length=50), nullable=False),
        sa.Column("category", sa.String(length=100), nullable=False),
        sa.Column("message", sa.String(), nullable=False),
        sa.Column("suggestion", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("github_comment_id", sa.BigInteger(), nullable=True),
        sa.Column("posted", sa.Boolean(), nullable=False),
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
            ["run_id"],
            ["session_target_runs.id"],
            name=op.f("fk_findings_run_id_session_target_runs"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["target_id"],
            ["session_targets.id"],
            name=op.f("fk_findings_target_id_session_targets"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_findings")),
    )
    with op.batch_alter_table("findings", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_findings_run_id"), ["run_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_findings_target_id"), ["target_id"], unique=False)

    op.create_table(
        "usage_records",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=True),
        sa.Column("target_id", sa.Uuid(), nullable=True),
        sa.Column("model_id", sa.String(length=255), nullable=False),
        sa.Column("provider", sa.String(length=100), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False),
        sa.Column("completion_tokens", sa.Integer(), nullable=False),
        sa.Column("total_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Numeric(precision=12, scale=6), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["review_sessions.id"],
            name=op.f("fk_usage_records_session_id_review_sessions"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["target_id"],
            ["session_targets.id"],
            name=op.f("fk_usage_records_target_id_session_targets"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_usage_records_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_usage_records")),
    )
    with op.batch_alter_table("usage_records", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_usage_records_session_id"),
            ["session_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_usage_records_target_id"),
            ["target_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_usage_records_workspace_id"),
            ["workspace_id"],
            unique=False,
        )

    op.create_table(
        "audit_logs",
        sa.Column("workspace_id", sa.Uuid(), nullable=True),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("target_type", sa.String(length=100), nullable=False),
        sa.Column("target_id", sa.Uuid(), nullable=True),
        sa.Column("detail", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name=op.f("fk_audit_logs_actor_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_audit_logs_workspace_id_workspaces"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_logs")),
    )
    with op.batch_alter_table("audit_logs", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_audit_logs_actor_user_id"),
            ["actor_user_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_audit_logs_workspace_id"),
            ["workspace_id"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("audit_logs", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_audit_logs_workspace_id"))
        batch_op.drop_index(batch_op.f("ix_audit_logs_actor_user_id"))
    op.drop_table("audit_logs")

    with op.batch_alter_table("usage_records", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_usage_records_workspace_id"))
        batch_op.drop_index(batch_op.f("ix_usage_records_target_id"))
        batch_op.drop_index(batch_op.f("ix_usage_records_session_id"))
    op.drop_table("usage_records")

    with op.batch_alter_table("findings", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_findings_target_id"))
        batch_op.drop_index(batch_op.f("ix_findings_run_id"))
    op.drop_table("findings")

    with op.batch_alter_table("session_target_runs", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_session_target_runs_target_id"))
    op.drop_table("session_target_runs")

    with op.batch_alter_table("session_targets", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_session_targets_session_id"))
        batch_op.drop_index(batch_op.f("ix_session_targets_repository_id"))
    op.drop_table("session_targets")

    with op.batch_alter_table("review_sessions", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_review_sessions_workspace_id"))
        batch_op.drop_index(batch_op.f("ix_review_sessions_triggered_by_user_id"))
    op.drop_table("review_sessions")

    with op.batch_alter_table("model_assignments", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_model_assignments_workspace_id"))
        batch_op.drop_index(batch_op.f("ix_model_assignments_model_catalog_id"))
    op.drop_table("model_assignments")

    with op.batch_alter_table("model_catalog", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_model_catalog_workspace_id"))
        batch_op.drop_index(batch_op.f("ix_model_catalog_credential_id"))
    op.drop_table("model_catalog")

    with op.batch_alter_table("provider_credentials", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_provider_credentials_workspace_id"))
    op.drop_table("provider_credentials")

    with op.batch_alter_table("repositories", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_repositories_workspace_id"))
        batch_op.drop_index(batch_op.f("ix_repositories_installation_id"))
    op.drop_table("repositories")

    with op.batch_alter_table("github_installations", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_github_installations_workspace_id"))
    op.drop_table("github_installations")

    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_users_workspace_id"))
    op.drop_table("users")

    op.drop_table("workspaces")
