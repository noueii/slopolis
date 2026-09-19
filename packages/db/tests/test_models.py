"""Model tests: schema creation and round-trip against in-memory SQLite."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from slopolis_db.models import (
    AuditLog,
    Finding,
    GitHubInstallation,
    ModelAssignment,
    ModelCatalog,
    ProviderCredential,
    Repository,
    ReviewSession,
    SessionTarget,
    SessionTargetRun,
    UsageRecord,
    User,
    Workspace,
)


async def test_full_graph_round_trip(session: AsyncSession) -> None:
    # Given a workspace, user, installation, repository, and review session
    workspace = Workspace(name="Acme", slug="acme")
    session.add(workspace)
    await session.flush()

    user = User(
        workspace_id=workspace.id,
        github_id=1001,
        handle="octocat",
        name="Mona Lisa",
        avatar_url=None,
    )
    installation = GitHubInstallation(
        workspace_id=workspace.id,
        installation_id=555,
        account_login="acme",
        account_type="Organization",
    )
    session.add_all([user, installation])
    await session.flush()

    repository = Repository(
        workspace_id=workspace.id,
        installation_id=installation.id,
        github_id=4242,
        full_name="acme/widgets",
        private=True,
        default_branch="main",
    )
    session.add(repository)
    await session.flush()

    review = ReviewSession(
        workspace_id=workspace.id,
        title="Review PR 7",
        name="pr-7",
        prompt="Focus on security",
        status="queued",
        model="gpt-4o-mini",
        provider="openai",
        triggered_by_user_id=user.id,
    )
    session.add(review)
    await session.flush()

    target = SessionTarget(
        session_id=review.id,
        repository_id=repository.id,
        number=7,
        title="Add login",
        url="https://github.com/acme/widgets/pull/7",
        head_branch="feature/login",
        status="queued",
    )
    session.add(target)
    await session.flush()

    run = SessionTargetRun(target_id=target.id, status="running")
    session.add(run)
    await session.flush()

    finding = Finding(
        target_id=target.id,
        run_id=run.id,
        path="src/auth.py",
        line=42,
        severity="error",
        category="security",
        message="SQL injection risk",
        suggestion="Use parameterized queries",
        confidence=0.91,
    )
    usage = UsageRecord(
        workspace_id=workspace.id,
        session_id=review.id,
        target_id=target.id,
        model_id="gpt-4o-mini",
        provider="openai",
        prompt_tokens=1200,
        completion_tokens=300,
        total_tokens=1500,
        cost_usd=Decimal("0.012345"),
    )
    session.add_all([finding, usage])
    await session.commit()

    # When the session is read back from a fresh query
    result = await session.execute(
        select(ReviewSession)
        .where(ReviewSession.id == review.id)
        .options(
            selectinload(ReviewSession.targets).selectinload(SessionTarget.findings),
            selectinload(ReviewSession.targets).selectinload(SessionTarget.runs),
        )
    )
    reloaded = result.scalar_one()
    assert reloaded.prompt == "Focus on security"
    assert reloaded.triggered_by_user_id == user.id

    targets = reloaded.targets
    assert len(targets) == 1
    assert targets[0].number == 7
    assert targets[0].tokens == 0
    assert targets[0].cost_usd == Decimal("0")
    assert targets[0].duration_ms is None

    findings = targets[0].findings
    assert len(findings) == 1
    assert findings[0].severity == "error"
    assert findings[0].posted is False
    assert findings[0].confidence == pytest.approx(0.91)

    runs = targets[0].runs
    assert len(runs) == 1
    assert runs[0].attempt == 1
    assert runs[0].tokens == 0
    assert runs[0].cost_usd == Decimal("0")

    stored_usage = await session.get(UsageRecord, usage.id)
    assert stored_usage is not None
    assert stored_usage.total_tokens == 1500
    assert stored_usage.cost_usd == Decimal("0.012345")


async def test_defaults_and_timestamps(session: AsyncSession) -> None:
    # Given a minimal workspace row
    workspace = Workspace(name="Solo", slug="solo")
    session.add(workspace)
    await session.commit()
    await session.refresh(workspace)

    # Then the UUID primary key, timestamps, and server defaults are populated
    assert isinstance(workspace.id, uuid.UUID)
    assert workspace.created_at is not None
    assert workspace.updated_at is not None


async def test_provider_and_model_configuration(session: AsyncSession) -> None:
    # Given a workspace with a credential, catalog entry, and assignment
    workspace = Workspace(name="Acme", slug="acme")
    session.add(workspace)
    await session.flush()

    credential = ProviderCredential(
        workspace_id=workspace.id,
        provider="openai",
        base_url="https://api.openai.com/v1",
        encrypted_api_key=b"cipher-text",
        key_last4="abcd",
    )
    session.add(credential)
    await session.flush()

    catalog = ModelCatalog(
        workspace_id=workspace.id,
        credential_id=credential.id,
        model_id="gpt-4o-mini",
        provider="openai",
        display_name="GPT-4o mini",
        source="imported",
    )
    session.add(catalog)
    await session.flush()

    assignment = ModelAssignment(
        workspace_id=workspace.id,
        role="review",
        model_catalog_id=catalog.id,
        model_id=None,
    )
    session.add(assignment)
    await session.commit()

    # Then defaults and nullable fields round-trip correctly
    await session.refresh(credential)
    assert credential.enabled is True
    assert credential.last_status is None
    assert credential.last_checked_at is None
    assert credential.encrypted_api_key == b"cipher-text"

    await session.refresh(assignment)
    assert assignment.model_id is None
    assert assignment.model_catalog_id == catalog.id


async def test_audit_log_nullable_workspace(session: AsyncSession) -> None:
    # Given an audit entry with no workspace (system-level action)
    entry = AuditLog(
        workspace_id=None,
        actor_user_id=None,
        action="credential.created",
        target_type="provider_credential",
        target_id=uuid.uuid4(),
        detail={"provider": "openai"},
    )
    session.add(entry)
    await session.commit()
    await session.refresh(entry)

    # Then nullable relations and JSON detail are preserved
    assert entry.workspace_id is None
    assert entry.detail == {"provider": "openai"}
    assert entry.created_at is not None
