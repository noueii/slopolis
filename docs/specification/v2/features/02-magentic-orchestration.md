# 11.2 Harness V2 — Magentic orchestration (PARKED)

> **STATUS: PARKED — for further investigation.** This is the Magentic ledger design (Task + Progress ledgers, stall→re-plan, coverage-based completion). It is **not** what Harness V1 implements — see [`01-supervisor-subagents.md`](./01-supervisor-subagents.md). Kept as a readiness/reference spec.

- **Scope:** Phase 3 (later). One Orchestrator per PR target, Magentic-style, directing specialist sub-agents.
- **Pattern:** Task Ledger (facts/guesses/plan) + Progress Ledger (per-step progress/completion); inner loop assigns, outer loop re-plans on stall.
- **Control flow is deterministic and lives in code.** The model only populates ledgers as strict JSON. Completion is a deterministic coverage check, not model self-report.
- **Models:** every agent resolves its model through workspace `ModelAssignment` by agent role. No model names in code or repo config.
- **Bounds:** explicit policy (steps/stalls/replans/depth) plus token/cost/wall-clock budgets, enforced before each step, with cooperative cancellation between steps.
- **Persistence/streaming:** every step and ledger snapshot is persisted and streamed over SSE.
- **Security:** repo/PR content is untrusted data; per-agent tool allow-list; read-only tools in v2; no auto-approve.

---

## 1. Goals / non-goals

**Goals**
1. Turn a PR target into a plan, execute it via specialists, and terminate on an objective coverage checklist.
2. Recover from stalls by re-planning rather than looping.
3. Keep models swappable per role and fully workspace-pinned.
4. Make orchestration observable, replayable, and bounded.
5. Degrade gracefully: a failed specialist degrades coverage, never the whole session.

**Non-goals (v2)**
- Code generation / autofix (findings + suggestions only).
- Merge authority (advisory only; no auto-approve).
- Clone-backed context or code execution (read-only GitHub API tools; clone remains behind the `ContextProvider` seam).
- Cross-PR integration (Phase 3c).

---

## 2. Core concepts

| Concept | Meaning |
|---|---|
| **Orchestrator** | Per-PR lead agent. Plans, assesses progress, assigns subtasks, re-plans. |
| **Task Ledger** | `facts` (evidence-backed), `guesses` (to verify), `plan` (aspect steps). |
| **Progress Ledger** | `coverage` per aspect, findings count, `is_progressing`, `next` assignment. |
| **Aspect** | A review concern: `context`, `logic`, `security`, `performance`, `tests`, `api_compat`, `docs`. |
| **Specialist** | An agent bound to a role; executes one `Assignment` and returns one `StepResult`. |
| **Coverage** | `none | partial | done` per aspect. Drives completion. |
| **Stall** | No new facts/findings, repeated assignment, or unchanged coverage for `max_stalls` steps → re-plan. |

---

## 3. Data models (`packages/core/orchestration/state.py`)

Pydantic v2, strict-typed (basedpyright strict; no `Any`).

```python
from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field


class Aspect(StrEnum):
    CONTEXT = "context"
    LOGIC = "logic"
    SECURITY = "security"
    PERFORMANCE = "performance"
    TESTS = "tests"
    API_COMPAT = "api_compat"
    DOCS = "docs"


class EvidenceKind(StrEnum):
    FILE = "file"
    DIFF_HUNK = "diff_hunk"
    TOOL_RESULT = "tool_result"
    ISSUE = "issue"
    DOC = "doc"


class EvidenceRef(BaseModel):
    model_config = ConfigDict(frozen=True)
    kind: EvidenceKind
    ref: str            # e.g. "src/api/users.py:120" or a tool-call id


class Fact(BaseModel):
    claim: str
    evidence: EvidenceRef
    observed_at: datetime


class Guess(BaseModel):
    claim: str
    to_verify: str


class PlanStep(BaseModel):
    id: str
    aspect: Aspect
    objective: str
    status: Literal["pending", "in_progress", "done", "skipped"] = "pending"
    owner_hint: str | None = None  # role name, not a credential


class TaskLedger(BaseModel):
    version: int = 0
    facts: list[Fact] = Field(default_factory=list)
    guesses: list[Guess] = Field(default_factory=list)
    plan: list[PlanStep] = Field(default_factory=list)


class Assignment(BaseModel):
    agent: str          # registry key
    aspect: Aspect
    subtask: str
    rationale: str


class StepResult(BaseModel):
    step: int
    agent: str
    aspect: Aspect
    subtask: str
    status: Literal["ok", "error", "skipped"]
    summary: str
    findings: list[Finding] = Field(default_factory=list)  # Finding lives in packages/core/review
    tokens: int
    cost_usd: float
    duration_ms: int
    model_id: str
    prompt_hash: str


class ProgressLedger(BaseModel):
    step: int
    coverage: dict[Aspect, Literal["none", "partial", "done"]]
    findings_count: int
    is_progressing: bool
    next: Assignment | None = None
    stall_reason: str | None = None


class Budget(BaseModel):
    max_steps: int
    max_tokens: int
    max_cost_usd: float
    max_wallclock_s: int
    steps_used: int = 0
    tokens_used: int = 0
    cost_used_usd: float = 0.0
    started_at: datetime

    def ok(self) -> bool:  # see §7 for the concrete implementation
        ...


class Policy(BaseModel):
    max_steps: int = 24
    max_stalls: int = 2
    max_replans: int = 2
    max_depth: int = 1
    max_concurrent_context_gathers: int = 3
    max_tokens: int = 200_000
    max_cost_usd: float = 1.50
    max_wallclock_s: int = 600
    allow_repo_content_instructions: bool = False


class RunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    ESCALATED = "escalated"   # partial result with coverage gaps
    FAILED = "failed"
    CANCELLED = "cancelled"


class RunState(BaseModel):
    target_ref: str
    task_ledger: TaskLedger | None = None
    progress: ProgressLedger | None = None
    transcript: list[StepResult] = Field(default_factory=list)
    policy: Policy
    budget: Budget
    replans: int = 0
    stalls: int = 0
    status: RunStatus = RunStatus.PENDING


class ReviewResult(BaseModel):
    findings: list[Finding]
    coverage: dict[Aspect, Literal["none", "partial", "done"]]
    status: RunStatus
    notes: list[str] = Field(default_factory=list)  # e.g. diff-only fallback
```

**Ledger delta operations** (the only mutations; the model never rewrites a ledger wholesale):

```python
def add_fact(ledger: TaskLedger, fact: Fact) -> TaskLedger: ...
def add_guess(ledger: TaskLedger, guess: Guess) -> TaskLedger: ...
def upsert_plan_step(ledger: TaskLedger, step: PlanStep) -> TaskLedger: ...
def mark_step(ledger: TaskLedger, step_id: str, status: str) -> TaskLedger: ...
```

---

## 4. Orchestrator interface (`packages/core/orchestration/orchestrator.py`)

```python
class EventSink(Protocol):
    async def emit(self, event: "OrchestrationEvent") -> None: ...


class Planner(Protocol):
    """LLM-backed. Each method returns strict JSON, schema-validated."""
    async def plan(self, target: TargetSpec, state: RunState) -> TaskLedger: ...
    async def assess(self, state: RunState) -> ProgressLedger: ...
    async def replan(self, state: RunState) -> TaskLedger: ...


class Orchestrator(Protocol):
    async def run(
        self, target: TargetSpec, *, state: RunState, emit: EventSink
    ) -> ReviewResult: ...
```

**Loop** (deterministic transitions; no LLM in the control path):

```python
async def run(target, *, state, emit) -> ReviewResult:
    state.task_ledger = await planner.plan(target, state)
    await emit(Event.plan_created(state.task_ledger))

    while state.status == RUNNING and state.budget.ok():
        state.progress = await planner.assess(state)
        await emit(Event.ledger_updated(state.progress))

        if completion_check(state.task_ledger, state):
            state.status = RunStatus.DONE
            break

        if stalled(state):
            if state.replans >= state.policy.max_replans:
                state.status = RunStatus.ESCALATED
                break
            await emit(Event.replanned(state.replans))
            state.task_ledger = await planner.replan(state)
            state.stalls = 0
            state.replans += 1
            continue

        assignment = state.progress.next
        if assignment is None or not allowed(assignment, state.policy):
            state.status = RunStatus.ESCALATED
            break

        result = await registry[assignment.agent].run(assignment, state)
        state.transcript.append(result)
        state.budget.charge(result)
        await emit(Event.step_finished(result))
        state.stalls = 0 if progressed(result) else state.stalls + 1

    return emitter.build(state)
```

### Stall detection (deterministic, not just the model's flag)

```python
def stalled(state: RunState) -> bool:
    if state.progress is None:
        return False
    if not state.progress.is_progressing:
        return True
    # deterministic backstops:
    if no_new_facts_or_findings(state, lookback=2):
        return True
    if repeated_assignment(state, lookback=2):
        return True
    if coverage_unchanged(state, lookback=3):
        return True
    return False
```

### Completion (the termination oracle)

```python
def completion_check(ledger: TaskLedger, state: RunState) -> bool:
    required = {s.aspect for s in ledger.plan}          # all planned aspects required in v2
    coverage = state.progress.coverage if state.progress else {}
    if not all(coverage.get(a) == "done" for a in required):
        return False
    return all(finding_is_grounded(f, state) for f in collected_findings(state))
```

`finding_is_grounded` reuses the v1 grounding rules: path exists in the change set, `line` is within a diff hunk, and any `suggestion` anchors to valid lines.

---

## 5. Agent registry & model resolution

`packages/core/orchestration/registry.py`:

```python
class AgentSpec(BaseModel):
    name: str
    role: str                                   # ModelAssignment role key
    system_prompt: str
    tools: list[str]                            # allow-list (read-only in v2)
    model_role: str = "review.specialist"       # resolved via ModelAssignment
    max_steps: int = 6


class AgentRegistry:
    def __init__(self, specs: list[AgentSpec], models: ModelResolver) -> None: ...
    def __getitem__(self, name: str) -> "SpecialistAgent": ...
    def allowed(self, name: str) -> bool: ...
```

`ModelResolver` maps `model_role` → `ModelAssignment` (workspace) → provider + model id from the model catalog, then to the LiteLLM gateway. Built-in roles:

| Role | Default `model_role` | Notes |
|---|---|---|
| `review.orchestrator` | `review.orchestrator` | Strongest reasoning role; used for plan/assess/replan |
| `review.logic` | `review.specialist` | Bug/logic analysis |
| `review.security` | `review.specialist` | Injection/authz/secrets/SSRF |
| `review.tests` | `review.specialist` | Test coverage/quality |
| `review.context` | `review.fast` | Cheap context gathering |

No model names appear in code or in `.codereview/agents/` — only roles.

---

## 6. Configuration

### Repo — `.codereview/agents/*.yml` (intent only; validated by Pydantic v2)

```yaml
version: 1
agents:
  - name: SecurityReviewer
    role: security
    model_role: review.security
    enabled: true
    tools: [read_file, list_dir, search_symbol]
    instructions: |
      Focus on injection, authorization, secret exposure, SSRF.
      Do not flag style or naming.
```

### Repo — `.codereview/config.yml`

```yaml
version: 1
orchestration:
  aspects: [context, logic, security, tests]
  policy:
    max_steps: 24
    max_cost_usd: 1.50
```

**Still forbidden in repo config:** model names, credentials, tool expansion beyond the allow-list, anything that changes policy in a way that bypasses workspace budgets. Workspace config may override repo policy downward only.

---

## 7. Policy defaults & budget

| Setting | Default | Rationale |
|---|---|---|
| `max_steps` | 24 | Bounds an inner loop without starving coverage |
| `max_stalls` | 2 | Re-plan before thrashing |
| `max_replans` | 2 | Then escalate with a partial result |
| `max_depth` | 1 | No recursive sub-agents in v2 |
| `max_concurrent_context_gathers` | 3 | Bounded fan-out for independent gathers |
| `max_tokens` | 200_000 | Per target |
| `max_cost_usd` | 1.50 | Per target; workspace may lower |
| `max_wallclock_s` | 600 | Hard stop; partial result on timeout |

`Budget.ok()` checks steps/tokens/cost/wall-clock; the loop checks it before every step, and cancellation is honored between steps (ARQ cancellation → `RunStatus.CANCELLED`).

---

## 8. Persistence (`packages/db`, Alembic migration)

| Table | Key columns |
|---|---|
| `agent_definition` | id, workspace_id, name, role, model_role, system_prompt, tools (JSONB), enabled, source (`builtin|repo`), created_at |
| `orchestration_run` | id, session_target_run_id FK, status, policy (JSONB), steps_used, tokens_used, cost_used_usd, replans, started_at, ended_at |
| `orchestration_step` | id, run_id FK, step_index, agent, aspect, subtask, status, summary, model_id, prompt_hash, tokens, cost_usd, duration_ms, created_at |
| `ledger_snapshot` | id, run_id FK, step_index, task_ledger (JSONB), progress_ledger (JSONB), created_at |
| `finding` | + nullable `orchestration_step_id` FK |

Indexes: `orchestration_step(run_id, step_index)`, `ledger_snapshot(run_id, step_index)`.

---

## 9. SSE events (`apps/server`)

`GET /sessions/{id}/events` (existing stream) gains run-scoped events:

| Event | Payload |
|---|---|
| `run.started` | run_id, target_ref, policy |
| `plan.created` | run_id, task_ledger |
| `step.started` | run_id, step_index, agent, aspect, subtask |
| `step.finished` | run_id, step_index, status, tokens, cost_usd, duration_ms |
| `ledger.updated` | run_id, step_index, progress_ledger |
| `finding.added` | run_id, step_index, finding |
| `run.replanned` | run_id, replans, reason |
| `run.stalled` | run_id, stall_reason |
| `run.completed` | run_id, status, coverage |
| `run.escalated` | run_id, coverage, notes |
| `run.failed` | run_id, error_code, message |

Payloads are Pydantic-serialized; the SPA renders plan + progress from `ledger.updated` and `step.*`.

---

## 10. Guardrails & security

- **Untrusted content is data.** Diff hunks, file contents, PR body, comments, and repo `instructions` are delimited and explicitly marked as untrusted; agents must never treat them as commands. `allow_repo_content_instructions=false` by default.
- **Tool allow-list per agent**; v2 tools are read-only (`read_file`, `list_dir`, `read_diff`, `search_symbol`). No clone, no exec, no write.
- **No side effects during orchestration.** Posting remains a separate, single publish step (v1 10.7) after the run completes.
- **No auto-approve.** Check run stays advisory unless a repo opts in (v1).
- **Budget before step.** A step that would exceed the budget is not started; the run escalates with a coverage note.
- **Injection screening.** Reuse the v1 grounding + sanitization filter (strip invisible/zero-width characters) before content enters a prompt.

---

## 11. Failure handling

| Failure | Behavior |
|---|---|
| Specialist error | Mark step `error`; orchestrator re-plans or skips the aspect; run continues with reduced coverage |
| Repeated stall | Re-plan up to `max_replans`, then `ESCALATED` with coverage gaps |
| Provider failure | Existing retry + fallback-model chain; if exhausted, escalate |
| Budget/timeout | Stop before the next step; return a partial `ReviewResult` with notes |
| Cancellation | ARQ cancel → `CANCELLED` between steps; persisted state is complete up to the last step |
| Grounding failure | Ungrounded findings are dropped before emit (never posted) |

Escalation always yields a usable, honestly-labeled partial review — never a silent failure.

---

## 12. Testing

- **Unit:** ledger deltas, `completion_check`, `stalled`, `Budget.ok`, `allowed`, grounding.
- **Integration:** orchestrator loop against scripted fake agents producing deterministic ledgers; assert step sequence, stall→replan, and termination.
- **Replay:** load `ledger_snapshot` rows, replay control flow with no LLM calls; assert identical decisions.
- **Review quality:** golden set of merged PRs + human comments; report precision, coverage, and actionability per agent/prompt change (closes the v1 §15 gap).
- **E2E:** sandbox repo, real GitHub App, assert plan/progress SSE and a posted review.

---

## 13. Build order recap

- **3a:** harness seam (`plan → act → assess` as a 1-step plan), `RunState`, budget, step events; wrap the v1 single-pass reviewer as the first specialist. No behavior change yet.
- **3b:** aspect specialists + registry + stall→replan + per-agent tool allow-lists.
- **3c:** session-level integration orchestrator, `.codereview/agents/` configurable agents, parallel context gathers.

---

## 14. Open questions

1. Final `.codereview/agents/` schema; are repo-supplied agent prompts allowed (trust), or must they be workspace-approval-gated?
2. Policy defaults by repo size/risk tier; who can raise them (admin only?).
3. Session integrator: separate orchestrator vs terminal step of each manager.
4. Scheduler for parallel context gathers under per-repo/global concurrency limits.
5. `ledger_snapshot` retention/compaction (potentially large JSONB).
