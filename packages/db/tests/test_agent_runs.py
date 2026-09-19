"""Agent run/event model tests: run-tree round trip, seq uniqueness, and linking."""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from slopolis_db.models import (
    AgentEventRow,
    AgentRun,
    Finding,
    GitHubInstallation,
    Repository,
    ReviewSession,
    SessionTarget,
    User,
    Workspace,
)


async def _seed_target(session: AsyncSession) -> SessionTarget:
    """Create a workspace → user → repo → session → target chain for a run tree."""
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
    return target


async def test_agent_run_tree_round_trip(session: AsyncSession) -> None:
    # Given a target and a main → PR → sub-agent run tree
    target = await _seed_target(session)

    main = AgentRun(
        session_id=target.session_id,
        target_id=None,
        parent_run_id=None,
        level="main",
        role="orchestrator.main",
        objective="Review every PR in this session",
        status="running",
    )
    session.add(main)
    await session.flush()

    pr = AgentRun(
        session_id=target.session_id,
        target_id=target.id,
        parent_run_id=main.id,
        level="pr",
        role="orchestrator.pr",
        model_id=None,
        objective="Review PR 7",
        status="running",
    )
    session.add(pr)
    await session.flush()

    child = AgentRun(
        session_id=target.session_id,
        target_id=target.id,
        parent_run_id=pr.id,
        level="sub",
        role="logic-reviewer",
        model_id="gpt-4o-mini",
        objective="Check auth logic",
        status="done",
        tokens=1500,
        cost_usd=Decimal("0.012345"),
    )
    session.add(child)
    event = AgentEventRow(
        run_id=pr.id,
        seq=1,
        type="agent.started",
        payload={"role": "orchestrator.pr", "target_id": str(target.id)},
    )
    session.add(event)
    await session.commit()

    # When the run tree is reloaded from the database
    result = await session.execute(
        select(AgentRun)
        .where(AgentRun.id == main.id)
        .options(selectinload(AgentRun.children).selectinload(AgentRun.children))
    )
    reloaded = result.scalar_one()

    # Then the hierarchy, defaults, and event payload round-trip correctly
    assert reloaded.parent_run_id is None
    assert reloaded.target_id is None
    assert reloaded.tokens == 0
    assert reloaded.cost_usd == Decimal("0")
    assert reloaded.started_at is None
    assert reloaded.ended_at is None
    assert reloaded.created_at is not None

    children = reloaded.children
    assert len(children) == 1
    assert children[0].id == pr.id
    assert children[0].parent_run_id == main.id
    assert children[0].target_id == target.id
    assert len(children[0].children) == 1
    assert children[0].children[0].id == child.id
    assert children[0].children[0].role == "logic-reviewer"
    assert children[0].children[0].tokens == 1500
    assert children[0].children[0].cost_usd == Decimal("0.012345")

    stored_event = await session.get(AgentEventRow, event.id)
    assert stored_event is not None
    assert stored_event.run_id == pr.id
    assert stored_event.seq == 1
    assert stored_event.type == "agent.started"
    assert stored_event.payload == {"role": "orchestrator.pr", "target_id": str(target.id)}
    assert stored_event.created_at is not None


async def test_agent_run_parent_back_reference(session: AsyncSession) -> None:
    # Given a parent run and a child that points at it
    target = await _seed_target(session)
    parent = AgentRun(
        session_id=target.session_id,
        level="pr",
        role="orchestrator.pr",
        objective="Review PR 7",
        status="running",
    )
    session.add(parent)
    await session.flush()

    child = AgentRun(
        session_id=target.session_id,
        parent_run_id=parent.id,
        level="sub",
        role="security-reviewer",
        objective="Check injection risks",
        status="pending",
    )
    session.add(child)
    await session.commit()

    # When the child is reloaded through its parent relationship
    result = await session.execute(
        select(AgentRun).where(AgentRun.id == child.id).options(selectinload(AgentRun.parent))
    )
    reloaded = result.scalar_one()

    # Then the back-reference resolves to the parent run
    assert reloaded.parent is not None
    assert reloaded.parent.id == parent.id
    assert reloaded.status == "pending"


async def test_agent_event_seq_is_unique_per_run(session: AsyncSession) -> None:
    # Given a run with one persisted event
    target = await _seed_target(session)
    run = AgentRun(
        session_id=target.session_id,
        level="sub",
        role="logic-reviewer",
        objective="Check auth logic",
        status="running",
    )
    session.add(run)
    await session.flush()
    session.add(AgentEventRow(run_id=run.id, seq=1, type="agent.started", payload={}))
    await session.commit()

    # When a second event reuses (run_id, seq)
    session.add(AgentEventRow(run_id=run.id, seq=1, type="agent.step", payload={}))

    # Then the unique constraint rejects it
    with pytest.raises(IntegrityError):
        await session.flush()
    await session.rollback()


async def test_finding_links_agent_run(session: AsyncSession) -> None:
    # Given a finding attributed to an agent run and one with no attribution
    target = await _seed_target(session)
    run = AgentRun(
        session_id=target.session_id,
        target_id=target.id,
        level="sub",
        role="logic-reviewer",
        objective="Check auth logic",
        status="done",
    )
    session.add(run)
    await session.flush()

    linked = Finding(
        target_id=target.id,
        agent_run_id=run.id,
        path="src/auth.py",
        line=42,
        severity="error",
        category="security",
        message="SQL injection risk",
        suggestion="Use parameterized queries",
        confidence=0.91,
    )
    unattributed = Finding(
        target_id=target.id,
        path="src/util.py",
        line=None,
        severity="info",
        category="style",
        message="Naming nit",
        suggestion=None,
        confidence=0.5,
    )
    session.add_all([linked, unattributed])
    await session.commit()

    # Then the nullable FK round-trips both ways
    stored_linked = await session.get(Finding, linked.id)
    stored_unattributed = await session.get(Finding, unattributed.id)
    assert stored_linked is not None
    assert stored_linked.agent_run_id == run.id
    assert stored_unattributed is not None
    assert stored_unattributed.agent_run_id is None
