"""Run-tree reads: the session's node hierarchy and its event log (spec v2 §7)."""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from slopolis_db.models import (
    AgentEventRow,
    AgentRun,
    Repository,
    ReviewSession,
    SessionTarget,
    User,
    Workspace,
)

from .conftest import (
    ApiHarness,
    FakeGateway,
    Seed,
    make_ref,
    seed_empty_workspace,
    seed_session,
)

#: The tree's own order is part of the contract, so the seeds pin their
#: timestamps instead of depending on when the test ran.
_T0 = dt.datetime(2026, 1, 1, 12, 0, tzinfo=dt.UTC)


async def _add_run(
    db: AsyncSession,
    session_id: uuid.UUID,
    *,
    level: str,
    role: str,
    started_at: dt.datetime | None,
    parent_run_id: uuid.UUID | None = None,
    target_id: uuid.UUID | None = None,
    status: str = "running",
    tokens: int = 0,
    cost: str = "0",
    objective: str = "do the work",
) -> AgentRun:
    """Persist one run row and return it (mirrors the worker's row shape)."""
    run = AgentRun(
        session_id=session_id,
        target_id=target_id,
        parent_run_id=parent_run_id,
        level=level,
        role=role,
        objective=objective,
        status=status,
        tokens=tokens,
        cost_usd=Decimal(cost),
        started_at=started_at,
    )
    db.add(run)
    await db.flush()
    return run


async def _add_event(
    db: AsyncSession, run_id: uuid.UUID, seq: int, kind: str = "agent.step"
) -> AgentEventRow:
    """Persist one event row at ``seq``."""
    event = AgentEventRow(
        run_id=run_id, seq=seq, type=kind, payload={"seq": seq, "role": "reviewer"}
    )
    db.add(event)
    await db.flush()
    return event


async def _tree(harness: ApiHarness, session_id: uuid.UUID) -> dict[str, Any]:
    """GET a session's run tree and return the parsed body."""
    response = await harness.client.get(f"/api/sessions/{session_id}/runs/tree")
    assert response.status_code == 200
    return response.json()


async def test_submitting_a_session_creates_its_main_run(
    seeded: Seed, build_harness: Any
) -> None:
    # Given a gateway that resolves one pull request
    url = "https://github.com/acme/api/pull/11"
    harness: ApiHarness = await build_harness(
        user_id=seeded.user_id,
        gateway=FakeGateway(refs={url: make_ref("acme/api", 11)}),
    )

    # When the session is submitted
    response = await harness.client.post("/api/sessions", json={"prUrls": [url]})

    # Then its run tree already exists, rooted at the session's main run
    assert response.status_code == 201
    body = await _tree(harness, uuid.UUID(response.json()["id"]))
    assert len(body["runs"]) == 1
    main = body["runs"][0]
    assert main["level"] == "main"
    assert main["role"] == "orchestrator.main"
    assert main["status"] == "running"
    assert main["parentRunId"] is None
    assert main["targetId"] is None
    assert main["startedAt"]
    assert main["children"] == []

    # ...whose objective names the submission, so the root node says what it is
    assert "acme/api#11" in main["objective"]


async def test_run_tree_nests_the_tree_with_counters(
    seeded: Seed,
    session_factory: async_sessionmaker[AsyncSession],
    build_harness: Any,
) -> None:
    # Given a finished review: main -> one PR -> two subs that started together
    # plus one that never started
    async with session_factory() as db:
        target_id = await db.scalar(
            select(SessionTarget.id).where(SessionTarget.session_id == seeded.session_id)
        )
        main = await _add_run(
            db,
            seeded.session_id,
            level="main",
            role="orchestrator.main",
            started_at=_T0,
            status="done",
            tokens=90,
            cost="0.300000",
        )
        pr = await _add_run(
            db,
            seeded.session_id,
            level="pr",
            role="orchestrator.pr",
            started_at=_T0 + dt.timedelta(seconds=1),
            parent_run_id=main.id,
            target_id=target_id,
            objective="Review PR #7",
            tokens=60,
            cost="0.200000",
        )
        first = await _add_run(
            db,
            seeded.session_id,
            level="sub",
            role="reviewer",
            started_at=_T0 + dt.timedelta(seconds=2),
            parent_run_id=pr.id,
            tokens=25,
            cost="0.050000",
        )
        tied = await _add_run(
            db,
            seeded.session_id,
            level="sub",
            role="reviewer",
            started_at=_T0 + dt.timedelta(seconds=2),
            parent_run_id=pr.id,
            status="failed",
            tokens=5,
            cost="0.010000",
        )
        pending = await _add_run(
            db,
            seeded.session_id,
            level="sub",
            role="reviewer",
            started_at=None,
            parent_run_id=pr.id,
        )
        await db.commit()
    harness: ApiHarness = await build_harness(user_id=seeded.user_id)

    # When the tree is read
    body = await _tree(harness, seeded.session_id)

    # Then it nests main -> pr -> sub, with the counters on the node that owns them
    assert [node["id"] for node in body["runs"]] == [str(main.id)]
    root = body["runs"][0]
    assert root["sessionId"] == str(seeded.session_id)
    assert root["level"] == "main"
    assert root["role"] == "orchestrator.main"
    assert root["parentRunId"] is None
    assert root["targetId"] is None
    assert root["status"] == "done"
    assert (root["tokens"], root["costUsd"]) == (90, 0.3)
    assert root["startedAt"].startswith("2026-01-01T12:00:00")

    pr_node = root["children"][0]
    assert pr_node["id"] == str(pr.id)
    assert pr_node["parentRunId"] == str(main.id)
    assert pr_node["targetId"] == str(target_id)
    assert pr_node["objective"] == "Review PR #7"
    assert (pr_node["tokens"], pr_node["costUsd"]) == (60, 0.2)

    # ...siblings ascend by startedAt, the tie falls back to id, and the node
    # with no startedAt is last
    ordered = sorted((first, tied), key=lambda run: str(run.id).replace("-", ""))
    assert [node["id"] for node in pr_node["children"]] == [
        *(str(run.id) for run in ordered),
        str(pending.id),
    ]
    assert [node["level"] for node in pr_node["children"]] == ["sub", "sub", "sub"]
    assert [node["status"] for node in pr_node["children"]] == [
        *(run.status for run in ordered),
        "running",
    ]


async def test_run_tree_returns_an_orphan_as_a_root(
    seeded: Seed,
    session_factory: async_sessionmaker[AsyncSession],
    build_harness: Any,
) -> None:
    # Given a tree whose PR node was deleted out from under its sub-agent, which
    # is how a pruning pass leaves a row behind
    async with session_factory() as db:
        main = await _add_run(
            db,
            seeded.session_id,
            level="main",
            role="orchestrator.main",
            started_at=_T0,
        )
        pr = await _add_run(
            db,
            seeded.session_id,
            level="pr",
            role="orchestrator.pr",
            started_at=_T0 + dt.timedelta(seconds=1),
            parent_run_id=main.id,
        )
        sub = await _add_run(
            db,
            seeded.session_id,
            level="sub",
            role="reviewer",
            started_at=_T0 + dt.timedelta(seconds=2),
            parent_run_id=pr.id,
        )
        await db.commit()
        await db.execute(delete(AgentRun).where(AgentRun.id == pr.id))
        await db.commit()
    harness: ApiHarness = await build_harness(user_id=seeded.user_id)

    # When the tree is read
    body = await _tree(harness, seeded.session_id)

    # Then the orphan is a root rather than a node dropped from the tree
    assert [node["id"] for node in body["runs"]] == [str(main.id), str(sub.id)]
    assert body["runs"][1]["parentRunId"] == str(pr.id)


async def test_run_tree_of_a_session_without_runs_is_empty(
    seeded: Seed, build_harness: Any
) -> None:
    # Given a session whose runs were never written (submitted before the
    # submit path created the main run)
    harness: ApiHarness = await build_harness(user_id=seeded.user_id)

    # When the tree is read
    body = await _tree(harness, seeded.session_id)

    # Then it is an empty tree, not an error
    assert body["runs"] == []


async def test_run_tree_hides_another_workspaces_session(
    seeded: Seed,
    session_factory: async_sessionmaker[AsyncSession],
    build_harness: Any,
) -> None:
    # Given another workspace's session
    async with session_factory() as db:
        workspace, member = await seed_empty_workspace(db)
        other = ReviewSession(
            workspace_id=workspace.id,
            title="Other review",
            name="widgets/app#1 - Jan 1",
            status="queued",
            model="claude-sonnet-4",
            provider="Anthropic",
            triggered_by_user_id=member.id,
        )
        db.add(other)
        await db.commit()
    harness: ApiHarness = await build_harness(user_id=seeded.user_id)

    # When its tree is requested by this workspace
    response = await harness.client.get(f"/api/sessions/{other.id}/runs/tree")

    # Then it is the standard 404, not an empty tree
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "session_not_found"


async def test_run_events_rejects_a_run_of_another_session(
    seeded: Seed,
    session_factory: async_sessionmaker[AsyncSession],
    build_harness: Any,
) -> None:
    # Given a run that belongs to a different session
    async with session_factory() as db:
        workspace = await db.get(Workspace, seeded.workspace_id)
        user = await db.get(User, seeded.user_id)
        repository = await db.get(Repository, seeded.repository_id)
        assert workspace is not None and user is not None and repository is not None
        other = await seed_session(
            db, workspace=workspace, user=user, repository=repository, number=8
        )
        run = await _add_run(
            db,
            other.id,
            level="main",
            role="orchestrator.main",
            started_at=_T0,
        )
        await _add_event(db, run.id, 1)
        await db.commit()
    harness: ApiHarness = await build_harness(user_id=seeded.user_id)

    # When its events are requested through this session
    response = await harness.client.get(
        f"/api/sessions/{seeded.session_id}/runs/{run.id}/events"
    )

    # Then the run is not readable as part of that session
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "run_not_found"


async def test_run_events_paginate_by_seq(
    seeded: Seed,
    session_factory: async_sessionmaker[AsyncSession],
    build_harness: Any,
) -> None:
    # Given a sub-agent run with five events, under a PR node
    async with session_factory() as db:
        main = await _add_run(
            db,
            seeded.session_id,
            level="main",
            role="orchestrator.main",
            started_at=_T0,
        )
        pr = await _add_run(
            db,
            seeded.session_id,
            level="pr",
            role="orchestrator.pr",
            started_at=_T0 + dt.timedelta(seconds=1),
            parent_run_id=main.id,
        )
        sub = await _add_run(
            db,
            seeded.session_id,
            level="sub",
            role="reviewer",
            started_at=_T0 + dt.timedelta(seconds=2),
            parent_run_id=pr.id,
        )
        for seq in range(1, 6):
            await _add_event(db, sub.id, seq)
        await db.commit()
        sub_id = sub.id
    harness: ApiHarness = await build_harness(user_id=seeded.user_id)
    url = f"/api/sessions/{seeded.session_id}/runs/{sub_id}/events"

    # When the log is read in pages
    first = await harness.client.get(url, params={"limit": 2})
    second = await harness.client.get(url, params={"afterSeq": first.json()["nextSeq"], "limit": 2})
    last = await harness.client.get(url, params={"afterSeq": second.json()["nextSeq"]})
    drained = await harness.client.get(url, params={"afterSeq": last.json()["nextSeq"]})

    # Then each page continues after the previous one and reports its own cursor
    assert [item["seq"] for item in first.json()["items"]] == [1, 2]
    assert first.json()["nextSeq"] == 2
    assert [item["seq"] for item in second.json()["items"]] == [3, 4]
    assert second.json()["nextSeq"] == 4
    assert [item["seq"] for item in last.json()["items"]] == [5]
    assert last.json()["nextSeq"] == 5

    # ...and a caught-up client gets an empty page with no cursor to pass back
    assert drained.json() == {"items": [], "nextSeq": None}

    # The items carry the event and the run's parent, which is how the UI groups
    # node detail under its PR
    item = first.json()["items"][0]
    assert set(item) == {
        "id",
        "runId",
        "parentRunId",
        "seq",
        "type",
        "payload",
        "createdAt",
    }
    assert item["runId"] == str(sub_id)
    assert item["parentRunId"] == str(pr.id)
    assert item["type"] == "agent.step"
    assert item["payload"] == {"seq": 1, "role": "reviewer"}
    assert item["createdAt"]
