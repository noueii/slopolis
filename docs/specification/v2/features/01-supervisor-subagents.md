# 11.1 Harness V1 — Hierarchical supervisor with sub-agents

- **Scope:** the harness we build first.
- **Hierarchy:** **Main Orchestrator** (one per session) → **PR Orchestrators** (one per PR target) → **review sub-agents** (spawned per PR as needed).
- **Uniform capability:** every agent at levels 0 and 1 can spawn sub-agents. Spawning is a normal tool call; depth is bounded by policy.
- **One PR orchestrator per PR target**, enforced by the runtime (1:1), not left to the model.
- **Visibility is first-class:** every spawn, step, tool call, message, finding, and failure is an event, persisted and streamed, rendered as a live run tree.
- **Models stay out of the harness:** each role resolves its model through workspace `ModelAssignment`.
- **Read-only during the run.** No code execution, no clone, no publishing until the run completes.

---

## 1. Goals / non-goals

**Goals**
1. Let a session with N PRs fan out into N independent PR orchestrators, each free to spawn the reviewers it decides it needs.
2. Allow different roles to use different models (e.g. a strong model for orchestrators, cheaper models for context gathering).
3. Give the user a live, legible view of the whole delegation tree.
4. Bound cost, concurrency, and recursion with explicit policy.
5. Degrade gracefully: one PR or one sub-agent failing never fails the session.

**Non-goals (V1)**
- Task/Progress ledgers, stall detection, re-planning (that is Harness V2, `02-magentic-orchestration.md`).
- Cross-PR reasoning beyond session aggregation (deferred).
- Clone-backed context, code execution, autofix, auto-approve.

---

## 2. Hierarchy & responsibilities

```
Session
 └─ Main Orchestrator            level=main   (1 per session)
     ├─ PR Orchestrator           level=pr     (1 per PR target, runtime-enforced)
     │   ├─ Context sub-agent     level=sub
     │   ├─ Logic sub-agent       level=sub
     │   ├─ Security sub-agent    level=sub
     │   └─ … as needed
     └─ PR Orchestrator (next PR) …
```

| Level | Role | Owns | Tools |
|---|---|---|---|
| 0 | Main Orchestrator | Session: spawn one PR orchestrator per target, aggregate, cross-PR synthesis | `spawn_subagents`, `list_targets`, `get_result` |
| 1 | PR Orchestrator | One PR: decide what to review, spawn sub-agents, merge/dedupe findings, emit PR result | `spawn_subagents`, read-only context tools |
| 2 | Review sub-agent | One scoped objective: analyze and return findings/summary | read-only context tools |

**Uniform capability:** `spawn_subagents` is available at levels 0 and 1. A level-2 sub-agent cannot spawn (blocked when `depth == policy.max_depth`). Raising `max_depth` is a policy change, not a code change.

**1:1 enforcement:** the runtime guarantees exactly one PR orchestrator per `SessionTarget`. If a model tries to spawn a second for the same target or omit one, the runtime corrects it (skip duplicate / spawn missing).

---

## 3. Roles & model resolution

`packages/core/harness/registry.py`

```python
class AgentSpec(BaseModel):
    name: str
    level: HarnessLevel                 # MAIN | PR | SUB
    model_role: str                     # resolved via workspace ModelAssignment
    system_prompt: str
    tools: list[str]                    # allow-list
    max_steps: int
    can_spawn: bool
```

Built-in roles (all model-bound via `ModelAssignment`, no model names in code):

| Role | Level | Default `model_role` | Spawns? |
|---|---|---|---|
| `orchestrator.main` | main | `harness.orchestrator` | yes |
| `orchestrator.pr` | pr | `harness.orchestrator` | yes |
| `reviewer` | sub | `review` | no |
| `context-gatherer` | sub | `review.fast` | no |
| `logic-reviewer` | sub | `review.specialist` | no |
| `security-reviewer` | sub | `review.security` | no |
| `test-reviewer` | sub | `review.tests` | no |

`ModelResolver` maps `model_role` → `ModelAssignment` (workspace) → provider+model from the model catalog → LiteLLM gateway. This is the single place models are chosen.

---

## 4. Spawn contract

Exposed to levels 0–1 as a tool. **Batch** by default so the model gets parallelism for free without managing handles.

```python
class Scope(BaseModel):
    target_id: UUID | None = None       # PR target (None for whole-session work)
    paths: list[str] = []               # files/paths of interest
    aspect: Aspect | None = None
    constraints: list[str] = []         # e.g. "do not flag style"


class SubtaskSpec(BaseModel):
    role: str                           # registry key
    objective: str
    scope: Scope = Scope()
    model_role: str | None = None       # optional per-call override of the role default


class SubAgentResult(BaseModel):
    run_id: UUID
    role: str
    status: AgentStatus                 # done | failed | cancelled
    summary: str
    findings: list[Finding] = []        # Finding lives in packages/core/review
    tokens_used: int
    cost_usd: float
    error: str | None = None


# tool signature presented to the model
async def spawn_subagents(tasks: list[SubtaskSpec]) -> list[SubAgentResult]: ...
```

Rules:
- `spawn_subagents` runs the tasks **concurrently** up to `policy.max_concurrent_children`, and **blocks** until all return; results come back in submission order.
- Each spawned run is recorded as a child `AgentRun` with `parent_run_id` set.
- Unknown roles and depth-exceeding spawns are rejected with a typed tool error (the model can recover).
- Sub-agents receive a **scoped task + minimal context**; they do not inherit the parent's full transcript (keeps tokens down and reasoning independent).

---

## 5. Orchestrator loop

Simpler than V2: no ledgers. Standard agent tool-loop with a mandatory deterministic termination check.

```
run(agent_spec, task, depth):
    run = create_run(...)                       # emit agent.spawned
    emit(agent.started)
    history = [system(agent_spec.system_prompt), user(task)]
    for step in range(agent_spec.max_steps):
        msg = await llm(model_for(agent_spec.model_role), history, tools=agent_spec.tools)
        emit(agent.step/msg)
        if msg.has_tool_calls:
            for call in msg.tool_calls:
                emit(agent.tool_call)
                result = await dispatch(call)      # spawn_subagents | read tools
                emit(agent.tool_result)
                history.append(tool_result(result))
        else:
            break                                  # model produced its final output
    result = finalize(run)                         # emit agent.completed
    return result
```

Termination is bounded by `max_steps` **and** by the level's budget. The PR orchestrator's final output is its merged `findings` + summary; the main orchestrator's final output is the session aggregate.

---

## 6. Budgets & limits (`Policy`)

| Setting | Default | Meaning |
|---|---|---|
| `max_depth` | 2 | main(0) → pr(1) → sub(2); sub-agents cannot spawn |
| `max_concurrent_pr_orchestrators` | 4 | PRs reviewed in parallel per session |
| `max_children_per_orchestrator` | 12 | Sub-agent spawns per orchestrator |
| `max_concurrent_children` | 4 | Parallelism within one orchestrator's `spawn_subagents` |
| `max_total_agent_runs` | 64 | Hard ceiling across the session |
| `max_steps_per_run` | per-role (`orchestrator.*` 12, reviewers 6) | Tool-loop cap |
| `max_tokens_per_run` | 60_000 | Per agent run |
| `max_cost_usd_per_run` | 0.40 | Per agent run |
| `max_session_cost_usd` | 5.00 | Whole session |
| `max_wallclock_s` | 900 | Whole session |

Enforcement: checked **before** each LLM call and each spawn; a run that would exceed its budget stops and returns a partial result with a note. `max_total_agent_runs` and session caps are enforced by a shared counter with a lock.

---

## 7. Visibility (the run tree)

### Events (single source of truth)

```python
class EventType(StrEnum):
    SPAWNED = "agent.spawned"
    STARTED = "agent.started"
    STEP = "agent.step"              # model turn (role, model_id, prompt/tool summary)
    TOOL_CALL = "agent.tool_call"    # tool name + args (redacted)
    TOOL_RESULT = "agent.tool_result"
    MESSAGE = "agent.message"        # assistant text/reasoning summary
    FINDING = "agent.finding"
    COMPLETED = "agent.completed"
    FAILED = "agent.failed"
    CANCELLED = "agent.cancelled"


class AgentEvent(BaseModel):
    id: UUID
    run_id: UUID
    seq: int
    type: EventType
    payload: dict            # shape per type; never contains secrets
    created_at: datetime
```

### API + streaming
- `GET /sessions/{id}/runs/tree` → nested `AgentRun` tree with status + counters.
- `GET /sessions/{id}/runs/{run_id}/events` → paginated event log (replay).
- `GET /sessions/{id}/events` (existing SSE) gains all `agent.*` events, tagged with `run_id`/`parent_run_id`.

### UI
- **Run tree pane:** Session → PRs → sub-agents, live status badges (running/done/failed), token/cost counters per node.
- **Node detail:** the node's event stream — steps, tool calls (+args/results), messages, findings.
- **Session summary:** aggregated findings by PR, total cost/tokens, coverage/limitations notes.

---

## 8. Persistence (`packages/db`, Alembic)

| Table | Columns |
|---|---|
| `agent_run` | id, session_id FK, target_id FK NULL, parent_run_id FK NULL (self), level, role, model_id, objective, status, tokens_used, cost_usd, started_at, ended_at, error |
| `agent_event` | id, run_id FK, seq, type, payload JSONB, created_at |
| `finding` | + nullable `agent_run_id` FK |

Indexes: `agent_run(session_id)`, `agent_run(parent_run_id)`, `agent_event(run_id, seq)`.
Retention: events are prunable per workspace retention policy (payloads already secret-redacted).

---

## 9. Configuration

Repo `.codereview.yml` (unchanged surface) may express **aspects to skip** and **instructions**; it may not add roles, expand tools, or name models. Workspace config may:
- define model roles / assignments (`ModelAssignment`),
- set policy values **downward** (tighter only),
- enable/disable built-in sub-agent roles.

Agent definitions are built-in in V1; workspace-level custom agents come later (was v1's Phase 3 backlog).

---

## 10. Guardrails & security

- **Read-only tools during the run:** `read_file`, `list_dir`, `read_diff`, `search_symbol`, `read_issue`. No clone, no exec, no write, no network.
- **Untrusted content is data.** Diffs, file contents, PR body, comments, and repo `instructions` are delimited and marked untrusted; sanitize invisible/zero-width characters before prompting.
- **Spawn allow-list:** only registry roles; args schema-validated; paths confined to the PR's changed files plus a bounded read budget.
- **No side effects until publish.** Posting remains a separate step after the run (v1 10.7); check run stays advisory.
- **Secrets never enter events.** Event payloads are constructed from typed fields, not raw model output; provider keys never reach a prompt.

---

## 11. Failure & cancellation

| Failure | Behavior |
|---|---|
| Sub-agent fails | Returned as `SubAgentResult(status=failed)`; the PR orchestrator may retry (bounded) or continue with partial coverage |
| PR orchestrator fails | Mark that target failed; other targets and the session continue |
| Main orchestrator fails | Session ends `failed` with per-target partials already persisted |
| Budget exceeded | Stop before the next LLM/spawn call; return partial result + note |
| Cancellation | Propagates down the tree (parent → children) between steps; all runs marked `cancelled`; partials retained |
| Rejected spawn | Typed tool error returned to the model; model may recover within its step budget |

---

## 12. Testing

- **Unit:** spawn tool (concurrency bound, order, rejection), budget accounting, tree assembly, event serialization/redaction.
- **Integration:** scripted fake LLMs that call `spawn_subagents`; assert tree shape, parallelism cap, 1:1 PR-orchestrator enforcement, cancellation propagation.
- **Replay:** reconstruct a run tree from `agent_event` rows and assert it matches.
- **E2E:** a session with 2 PRs in a sandbox repo; assert two PR orchestrators, sub-agent fan-out, posted review, and a complete tree.
- **Quality:** golden-PR set (the v1 §15 gap) — precision/coverage per role and per prompt/model change.

---

## 13. Build order

| Step | Deliverable |
|---|---|
| **V1.1** | `agent_run`/`agent_event` schema + persistence; run-tree API + SSE; main orchestrator spawns one PR orchestrator per target; PR orchestrator runs the existing single-pass review as one sub-agent. Observable end-to-end tree. |
| **V1.2** | `spawn_subagents` batch tool; PR orchestrator spawns multiple aspect sub-agents in parallel; findings merge + dedupe at the PR orchestrator. |
| **V1.3** | Full policy enforcement (depth/depth-2, concurrency, session cost), cancellation propagation, replay, node-detail UI, secret redaction audit. |
| **V1.4** | Quality eval harness; per-role model comparison; coverage notes surfaced in the summary. |

---

## 14. Open questions

1. Event granularity for `STEP`/`MESSAGE`: store a summary or the full model turn? (Storage vs diagnosability.)
2. Should sub-agents share a repo-context cache to cut tokens, or stay isolated for independence?
3. Retry semantics for failed sub-agents: fixed retry count vs model-decided.
4. Whether the main orchestrator should do more than aggregate (cross-PR synthesis) in V1.
5. Per-role `max_steps` tuning after first real sessions.

## 15. Implementation notes (V1.1)

§4 (one sub-job per target) and §13 (the main orchestrator spawns one PR orchestrator per target)
meet like this, and the queue keeps its existing shape:

- The **submit path** creates the session's `main` `AgentRun` (level `main`, one per session) while
  the session row is created, and enqueues the per-target jobs exactly as today. Enqueue-time
  creation is what makes the tree exist even when every target job fails to start.
- Each **target job** creates its own `pr` `AgentRun` with `parent_run_id` = the session's main run
  and `target_id` set. 1:1 is enforced by the runtime, not the model: a unique constraint on
  `(session_id, target_id)` for level `pr`, so a retried attempt reuses its run row rather than
  adding a second orchestrator.
- A target job **does not call the main orchestrator**; the main run is the session's aggregation
  point and is finished by the existing post-target session recompute once no target is left
  running. Cross-PR synthesis beyond aggregation stays deferred (§1).
- V1.1 spawns no sub-agents yet beyond the PR orchestrator's own reviewer: the PR orchestrator runs
  the existing single-pass harness (v1 10.6) as one `sub` run, which is what makes the tree
  observable end-to-end before `spawn_subagents` (V1.2) changes the fan-out.
- That one `sub` run is the built-in `reviewer` role (§3): level `sub`, model role `review`
  (the v1 single-pass reviewer), read-only tools, no spawning. V1.2 replaces it with the aspect
  sub-agents.
