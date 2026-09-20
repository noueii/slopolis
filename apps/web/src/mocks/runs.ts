/**
 * MSW handlers for the harness run tree (spec v2 §7).
 *
 * The tree is derived from the same dataset the Sessions list serves, so the
 * numbers stay coherent: a target's PR run carries part of that target's usage
 * and its sub-agents split the rest, and roles come from the registry's
 * built-ins (`orchestrator.main`, `orchestrator.pr`, the reviewer set).
 *
 * A run that failed and was retried becomes two sub-runs for the same role, one
 * per attempt — the shape that makes a failed sub-agent legible next to the
 * session that kept going.
 *
 * A running session's tree is not frozen: the session stream drains the live
 * script this module keeps (`peekLiveFrame`/`commitLiveFrame`), and every
 * delivered frame advances the same store the tree endpoint serves.
 */

import { HttpResponse, delay, http } from "msw"

import type {
  AgentEventItem,
  AgentEventPage,
  AgentRunNode,
  AgentRunStatus,
  AgentRunTreeResponse,
  ReviewSession,
  SessionTarget,
  TargetStatus,
} from "@/api/contract"
import { dataset } from "./dataset"

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "/api"

const MAIN_OBJECTIVE = "Coordinate the review of every target in this session"

const SUB_ROLES = [
  "context-gatherer",
  "logic-reviewer",
  "security-reviewer",
  "test-reviewer",
] as const

const FINDING_MESSAGES = [
  "Retry loop can spin without backoff when the provider answers 502",
  "Authorization check runs after the record is loaded from the store",
  "Token refresh window is narrower than the clock skew it guards against",
  "Unbounded buffer grows for the lifetime of the connection",
  "Error is swallowed and the caller sees a success response",
]

const FINDING_PATHS = [
  "src/api/gateway.ts",
  "src/worker/pool.ts",
  "src/auth/session.ts",
  "src/db/query.ts",
  "src/http/retry.ts",
]

const SUB_OBJECTIVES: Record<string, (target: SessionTarget) => string> = {
  "context-gatherer": (target) =>
    `Gather the diff and surrounding context for #${target.number}`,
  "logic-reviewer": (target) =>
    `Review control flow and edge cases in #${target.number}`,
  "security-reviewer": (target) =>
    `Check auth boundaries and secret handling in #${target.number}`,
  "test-reviewer": (target) => `Check test coverage for #${target.number}`,
}

const SESSION_STATUS_TO_RUN: Record<ReviewSession["status"], AgentRunStatus> = {
  queued: "pending",
  running: "running",
  done: "done",
  failed: "failed",
  cancelled: "cancelled",
}

const TARGET_STATUS_TO_RUN: Record<TargetStatus, AgentRunStatus | null> = {
  queued: null,
  skipped: null,
  running: "running",
  done: "done",
  failed: "failed",
  cancelled: "cancelled",
}

function hash(input: string): number {
  let value = 0
  for (let i = 0; i < input.length; i += 1) {
    value = (value * 31 + input.charCodeAt(i)) | 0
  }
  return Math.abs(value)
}

function iso(ms: number): string {
  return new Date(ms).toISOString()
}

interface SessionRuns {
  runs: AgentRunNode[]
  events: Map<string, AgentEventItem[]>
  /** Frames the session's live stream still has to deliver, in order. */
  live: LiveFrame[]
  /** Target statuses the live stream moved past the dataset's, by target id. */
  targetStatus: Map<string, TargetStatus>
}

const CACHE = new Map<string, SessionRuns>()

function sessionRuns(session: ReviewSession): SessionRuns {
  const cached = CACHE.get(session.id)
  if (cached) return cached
  const built = buildSessionRuns(session)
  CACHE.set(session.id, built)
  return built
}

const TERMINAL_EVENTS = {
  done: "agent.completed",
  failed: "agent.failed",
  cancelled: "agent.cancelled",
} as const

type TerminalStatus = keyof typeof TERMINAL_EVENTS

function isTerminalStatus(status: AgentRunStatus): status is TerminalStatus {
  return status === "done" || status === "failed" || status === "cancelled"
}

/** One run's event, numbered by the run's next free sequence. */
function eventWith(
  runId: string,
  parentRunId: string | null,
  seq: number,
  type: string,
  payload: Record<string, unknown>,
  createdAt: string,
): AgentEventItem {
  return {
    id: `${runId}_evt${seq}`,
    runId,
    parentRunId,
    seq,
    type,
    payload,
    createdAt,
  }
}

/** Append one event to its run's log. */
function appendEvent(
  events: Map<string, AgentEventItem[]>,
  event: AgentEventItem,
): void {
  const list = events.get(event.runId) ?? []
  list.push(event)
  events.set(event.runId, list)
}

/** One run's event log, numbering events after whatever the run already holds. */
class RunLog {
  constructor(
    private readonly events: Map<string, AgentEventItem[]>,
    private readonly runId: string,
    private readonly parentRunId: string | null,
  ) {}

  add(type: string, payload: Record<string, unknown>, createdAt: string): void {
    const seq = (this.events.get(this.runId)?.length ?? 0) + 1
    appendEvent(
      this.events,
      eventWith(this.runId, this.parentRunId, seq, type, payload, createdAt),
    )
  }
}

function buildSessionRuns(session: ReviewSession): SessionRuns {
  const events = new Map<string, AgentEventItem[]>()
  const startedMs = session.startedAt
    ? new Date(session.startedAt).getTime()
    : new Date(session.createdAt).getTime() + 4_000
  const mainId = `run_${session.id}_main`
  const mainStatus = SESSION_STATUS_TO_RUN[session.status]
  const mainLog = new RunLog(events, mainId, null)

  // The main orchestrator only spends tokens coordinating, so it takes a small
  // slice of the session's total; the PR runs and their sub-agents account for
  // the rest, which keeps the levels from double-counting the same work.
  const mainTokens =
    mainStatus === "pending" ? 0 : 900 + (hash(`${session.id}:main`) % 2_400)
  const mainCost = costOf(session, mainTokens)
  const mainEnded = isTerminalStatus(mainStatus)
    ? iso(startedMs + 20_000)
    : null

  const children: AgentRunNode[] = session.targets.flatMap((target, index) =>
    buildTargetRuns({
      session,
      target,
      index,
      mainId,
      mainStartedMs: startedMs,
      events,
    }),
  )

  if (mainStatus !== "pending") {
    mainLog.add(
      "agent.spawned",
      {
        role: "orchestrator.main",
        level: "main",
        parent_run_id: null,
        target_id: null,
        objective: MAIN_OBJECTIVE,
        model_role: "harness.orchestrator",
        depth: 0,
      },
      iso(startedMs),
    )
    mainLog.add(
      "agent.started",
      { role: "orchestrator.main", model_id: session.model, status: "running" },
      iso(startedMs + 900),
    )
    mainLog.add(
      "agent.step",
      {
        step: 1,
        model_id: session.model,
        summary: `Fanned out ${session.targetCount} PR ${session.targetCount === 1 ? "orchestrator" : "orchestrators"}.`,
        tool_call_count: 1,
      },
      iso(startedMs + 1_800),
    )
    mainLog.add(
      "agent.tool_call",
      {
        tool: "spawn_subagents",
        args_summary: `${session.targetCount} × orchestrator.pr`,
        step: 1,
        call_id: "call_main_spawn",
      },
      iso(startedMs + 2_400),
    )
    mainLog.add(
      "agent.tool_result",
      {
        tool: "spawn_subagents",
        ok: true,
        summary: `${children.length} orchestrators running`,
        step: 1,
        call_id: "call_main_spawn",
        result_count: children.length,
      },
      iso(startedMs + 3_000),
    )
    mainLog.add(
      "agent.message",
      {
        role: "assistant",
        summary:
          "Each target is reviewed by its own orchestrator; I aggregate their findings.",
        chars: 84,
      },
      iso(startedMs + 3_600),
    )
  }

  if (isTerminalStatus(mainStatus) && mainEnded) {
    const summary =
      mainStatus === "done"
        ? `Aggregated ${session.findingsCount} findings across ${session.targetCount} targets`
        : mainStatus === "failed"
          ? "Stopped before every target reported"
          : "Cancelled before every target reported"
    mainLog.add(
      TERMINAL_EVENTS[mainStatus],
      {
        status: mainStatus,
        summary,
        tokens_used: mainTokens,
        step_count: 3,
        ...(mainStatus === "failed" ? { error: "provider unreachable after retry" } : {}),
        ...(mainStatus === "done"
          ? { cost_usd: mainCost, finding_count: session.findingsCount }
          : {}),
      },
      mainEnded,
    )
  }

  const mainNode: AgentRunNode = {
    id: mainId,
    sessionId: session.id,
    targetId: null,
    parentRunId: null,
    level: "main",
    role: "orchestrator.main",
    modelId: mainStatus === "pending" ? null : session.model,
    objective: MAIN_OBJECTIVE,
    status: mainStatus,
    tokens: mainTokens,
    costUsd: mainCost,
    startedAt: mainStatus === "pending" ? null : iso(startedMs),
    endedAt: mainEnded,
    error:
      mainStatus === "failed" ? "provider unreachable after retry" : null,
    children,
  }

  return {
    runs: [mainNode],
    events,
    live: buildLiveFrames(session, mainNode, events),
    targetStatus: new Map(),
  }
}

interface TargetRunInput {
  session: ReviewSession
  target: SessionTarget
  index: number
  mainId: string
  mainStartedMs: number
  events: Map<string, AgentEventItem[]>
}

function buildTargetRuns({
  session,
  target,
  index,
  mainId,
  mainStartedMs,
  events,
}: TargetRunInput): AgentRunNode[] {
  const raw = TARGET_STATUS_TO_RUN[target.status]
  if (raw === null) return []
  const status: AgentRunStatus = raw
  const terminal: TerminalStatus | null = isTerminalStatus(raw) ? raw : null

  const prId = `run_${session.id}_${target.id}`
  const prStartedMs = mainStartedMs + 4_000 + index * 2_000
  const prLog = new RunLog(events, prId, mainId)
  const attempts = subAttempts(session, target, status)
  const sharing = shareUsage(target, attempts.length)

  prLog.add(
    "agent.spawned",
    {
      role: "orchestrator.pr",
      level: "pr",
      parent_run_id: mainId,
      target_id: target.id,
      objective: `Review PR #${target.number}: ${target.title}`,
      model_role: "harness.orchestrator",
      depth: 1,
    },
    iso(prStartedMs),
  )
  prLog.add(
    "agent.started",
    { role: "orchestrator.pr", model_id: session.model, status: "running" },
    iso(prStartedMs + 600),
  )
  prLog.add(
    "agent.step",
    {
      step: 1,
      model_id: session.model,
      summary: `Scoped ${attempts.length} ${attempts.length === 1 ? "review" : "reviews"} for this diff.`,
      tool_call_count: 1,
    },
    iso(prStartedMs + 1_200),
  )
  prLog.add(
    "agent.tool_call",
    {
      tool: "spawn_subagents",
      args_summary: attempts.map((attempt) => attempt.role).join(", "),
      step: 1,
      call_id: `${prId}_spawn`,
    },
    iso(prStartedMs + 1_800),
  )
  prLog.add(
    "agent.tool_result",
    {
      tool: "spawn_subagents",
      ok: true,
      summary: `${attempts.length} sub-agents returned`,
      step: 1,
      call_id: `${prId}_spawn`,
      result_count: attempts.length,
    },
    iso(prStartedMs + 2_400),
  )

  const children = attempts.map((attempt, attemptIndex) =>
    buildSubRun({
      session,
      target,
      prId,
      prStartedMs,
      attempt,
      attemptIndex,
      tokens: sharing.tokens[attemptIndex] ?? 0,
      costUsd: sharing.cost[attemptIndex] ?? 0,
      events,
    }),
  )

  const prEnded = terminal
    ? iso(prStartedMs + 8_000 + children.length * 3_000)
    : null
  if (terminal && prEnded) {
    prLog.add(
      TERMINAL_EVENTS[terminal],
      {
        status,
        summary:
          status === "done"
            ? `Merged ${target.findingsCount} findings from ${children.length} sub-agents`
            : status === "failed"
              ? "A sub-agent failed and no usable coverage was produced"
              : "Cancelled with the session",
        tokens_used: sharing.prTokens,
        step_count: 2,
        ...(status === "failed" ? { error: "sub-agent returned no findings" } : {}),
        ...(status === "done"
          ? { cost_usd: sharing.prCost, finding_count: target.findingsCount }
          : {}),
      },
      prEnded,
    )
  }

  const prNode: AgentRunNode = {
    id: prId,
    sessionId: session.id,
    targetId: target.id,
    parentRunId: mainId,
    level: "pr",
    role: "orchestrator.pr",
    modelId: session.model,
    objective: `Review PR #${target.number}: ${target.title}`,
    status,
    tokens: sharing.prTokens,
    costUsd: sharing.prCost,
    startedAt: iso(prStartedMs),
    endedAt: prEnded,
    error: status === "failed" ? "sub-agent returned no findings" : null,
    children,
  }

  return [prNode]
}

interface Attempt {
  role: string
  status: AgentRunStatus
}

/**
 * One attempt per sub-agent run. A failed attempt is followed by a retry of the
 * same role when the PR succeeded anyway (spec §11: the orchestrator may retry
 * or continue with partial coverage).
 */
function subAttempts(
  session: ReviewSession,
  target: SessionTarget,
  prStatus: AgentRunStatus,
): Attempt[] {
  const seed = hash(`${session.id}:${target.id}`)
  const first = SUB_ROLES[seed % 2]
  const second = SUB_ROLES[2 + (seed % 2)]

  if (prStatus === "running") {
    return [
      { role: first, status: "done" },
      { role: second, status: "running" },
    ]
  }
  if (prStatus === "failed") return [{ role: first, status: "failed" }]
  if (prStatus === "cancelled") return [{ role: first, status: "cancelled" }]

  const attempts: Attempt[] = [{ role: first, status: "done" }]
  if (seed % 4 === 0) {
    attempts.push({ role: second, status: "failed" })
    attempts.push({ role: second, status: "done" })
  } else {
    attempts.push({ role: second, status: "done" })
    if (seed % 3 === 0) {
      attempts.push({ role: SUB_ROLES[(seed + 1) % 4], status: "done" })
    }
  }
  return attempts
}

interface SubRunInput {
  session: ReviewSession
  target: SessionTarget
  prId: string
  prStartedMs: number
  attempt: Attempt
  attemptIndex: number
  tokens: number
  costUsd: number
  events: Map<string, AgentEventItem[]>
}

function buildSubRun({
  session,
  target,
  prId,
  prStartedMs,
  attempt,
  attemptIndex,
  tokens,
  costUsd,
  events,
}: SubRunInput): AgentRunNode {
  const id = `${prId}_sub${attemptIndex}`
  const log = new RunLog(events, id, prId)
  const startedMs = prStartedMs + 3_000 + attemptIndex * 6_000
  const ended = isTerminalStatus(attempt.status)
    ? iso(startedMs + 4_000 + (hash(id) % 5_000))
    : null
  const objective = SUB_OBJECTIVES[attempt.role]?.(target) ?? `Review #${target.number}`
  const findingCount = attempt.status === "done" ? hash(id) % 3 : 0

  log.add(
    "agent.spawned",
    {
      role: attempt.role,
      level: "sub",
      parent_run_id: prId,
      target_id: target.id,
      objective,
      model_role: "review",
      depth: 2,
    },
    iso(startedMs),
  )
  log.add(
    "agent.started",
    { role: attempt.role, model_id: session.model, status: "running" },
    iso(startedMs + 400),
  )
  log.add(
    "agent.step",
    {
      step: 1,
      model_id: session.model,
      summary: `Reading the changed files for ${target.repository.fullName}#${target.number}.`,
      tool_call_count: 1,
    },
    iso(startedMs + 1_000),
  )
  log.add(
    "agent.tool_call",
    {
      tool: "read_diff",
      args_summary: `${target.repository.fullName}#${target.number} (changed files)`,
      step: 1,
      call_id: `${id}_read`,
    },
    iso(startedMs + 1_400),
  )
  log.add(
    "agent.tool_result",
    {
      tool: "read_diff",
      ok: true,
      summary: `${8 + (hash(id) % 40)} changed files`,
      step: 1,
      call_id: `${id}_read`,
      result_count: 8 + (hash(id) % 40),
    },
    iso(startedMs + 2_000),
  )
  log.add(
    "agent.message",
    {
      role: "assistant",
      summary: `Reviewed the diff for ${target.title.toLowerCase()}.`,
      chars: 62,
    },
    iso(startedMs + 2_600),
  )

  for (let i = 0; i < findingCount; i += 1) {
    const seed = hash(`${id}:f${i}`)
    log.add(
      "agent.finding",
      {
        path: FINDING_PATHS[seed % FINDING_PATHS.length],
        line: 20 + (seed % 400),
        severity: i === 0 ? "high" : "medium",
        category: findingCategory(attempt.role),
        message: FINDING_MESSAGES[seed % FINDING_MESSAGES.length],
        suggestion: "Guard the call and surface the failure to the caller.",
        confidence: 0.6 + ((seed % 30) / 100),
      },
      iso(startedMs + 3_000 + i * 400),
    )
  }

  if (ended && isTerminalStatus(attempt.status)) {
    log.add(
      TERMINAL_EVENTS[attempt.status],
      {
        status: attempt.status,
        summary:
          attempt.status === "done"
            ? `Returned ${findingCount} ${findingCount === 1 ? "finding" : "findings"} for the PR orchestrator to merge`
            : attempt.status === "failed"
              ? "Stopped after the model returned nothing usable"
              : "Cancelled with its parent",
        tokens_used: tokens,
        step_count: 2,
        ...(attempt.status === "failed"
          ? { error: "provider returned an empty completion" }
          : {}),
        ...(attempt.status === "done"
          ? { cost_usd: costUsd, finding_count: findingCount }
          : {}),
      },
      ended,
    )
  }

  return {
    id,
    sessionId: session.id,
    targetId: target.id,
    parentRunId: prId,
    level: "sub",
    role: attempt.role,
    modelId: session.model,
    objective,
    status: attempt.status,
    tokens,
    costUsd,
    startedAt: iso(startedMs),
    endedAt: ended,
    error:
      attempt.status === "failed" ? "provider returned an empty completion" : null,
    children: [],
  }
}

function costOf(session: ReviewSession, tokens: number): number {
  if (session.tokens <= 0) return 0
  return Number(((session.costUsd / session.tokens) * tokens).toFixed(4))
}

/** Split a target's usage between its PR run and its sub-agent attempts. */
function shareUsage(
  target: SessionTarget,
  attemptCount: number,
): { prTokens: number; prCost: number; tokens: number[]; cost: number[] } {
  const prTokens = attemptCount === 0 ? target.tokens : Math.round(target.tokens * 0.3)
  const pool = Math.max(0, target.tokens - prTokens)
  const perAttempt = attemptCount === 0 ? 0 : Math.floor(pool / attemptCount)
  const tokens = Array.from({ length: attemptCount }, () => perAttempt)
  const costPerToken = target.tokens > 0 ? target.costUsd / target.tokens : 0
  return {
    prTokens,
    prCost: Number((costPerToken * prTokens).toFixed(4)),
    tokens,
    cost: tokens.map((value) => Number((costPerToken * value).toFixed(4))),
  }
}

/**
 * Live simulation for a running session (mock only).
 *
 * A real session's stream drains the events its worker keeps writing, and the
 * run tree is whatever that log holds at the moment a client reads it. The mock
 * has no worker, so its stream *is* the worker: each running session gets one
 * script that picks up where the stored tree left off and finishes the work
 * that tree still shows as running. The stream delivers one frame per read and
 * commits it to this same store, so the tree the client folds the frames onto
 * and the tree it reads back afterwards agree.
 */

/** Spacing between the simulated worker's events, in ms. */
export const LIVE_FRAME_INTERVAL_MS = 650

/** One frame of a session's live script. */
export interface LiveFrame {
  event: AgentEventItem
  /** Run the frame starts, for the frames that start one; `null` to patch one. */
  spawned: AgentRunNode | null
  /** Set once the store has advanced by the frame. */
  applied: boolean
}

/** The session's next undelivered frame, or `null` once its script is drained. */
export function peekLiveFrame(session: ReviewSession): LiveFrame | null {
  return sessionRuns(session).live.find((frame) => !frame.applied) ?? null
}

/** Advance the run store by one delivered frame. */
export function commitLiveFrame(session: ReviewSession, frame: LiveFrame): void {
  if (frame.applied) return
  frame.applied = true
  const state = sessionRuns(session)
  if (frame.spawned) {
    insertRun(state.runs, frame.spawned)
    if (frame.spawned.targetId) {
      state.targetStatus.set(frame.spawned.targetId, "running")
    }
  }
  appendEvent(state.events, frame.event)
  advanceRun(state.runs, frame.event)
}

/**
 * Target status the live stream has moved past the dataset's, or `null` when
 * the dataset still has it right. The streamed session projection reports
 * these, so a target whose orchestrator just started does not read `queued`
 * beside its running run.
 */
export function liveTargetStatus(
  session: ReviewSession,
  targetId: string,
): TargetStatus | null {
  return sessionRuns(session).targetStatus.get(targetId) ?? null
}

/** Insert a run the live stream spawned under the parent it announced. */
function insertRun(runs: AgentRunNode[], node: AgentRunNode): void {
  if (node.parentRunId === null) {
    runs.push(node)
    return
  }
  const parent = findRunNode(runs, node.parentRunId)
  if (parent) parent.children.push(node)
}

function findRunNode(runs: AgentRunNode[], id: string): AgentRunNode | null {
  for (const node of runs) {
    if (node.id === id) return node
    const found = findRunNode(node.children, id)
    if (found) return found
  }
  return null
}

/**
 * Fold one delivered frame onto its run: the model an `agent.started` resolved
 * and the state an `agent.completed` leaves behind. The simulation only ever
 * starts a run and completes it, so a progress event changes nothing else.
 */
function advanceRun(runs: AgentRunNode[], event: AgentEventItem): void {
  const node = findRunNode(runs, event.runId)
  if (!node) return
  const modelId = event.payload.model_id
  if (typeof modelId === "string") node.modelId = modelId
  if (event.type !== "agent.completed") return
  node.status = "done"
  node.endedAt = event.createdAt
  const tokens = event.payload.tokens_used
  if (typeof tokens === "number") node.tokens = tokens
  const costUsd = event.payload.cost_usd
  if (typeof costUsd === "number") node.costUsd = costUsd
}

/** The newest event time across a session's logs, where its script picks up. */
function latestEventMs(events: Map<string, AgentEventItem[]>): number {
  let newest = 0
  for (const rows of events.values()) {
    for (const row of rows) {
      const ms = new Date(row.createdAt).getTime()
      if (ms > newest) newest = ms
    }
  }
  return newest
}

/** The first sub-agent run still working, anywhere in the tree. */
function findRunningSubRun(runs: AgentRunNode[]): AgentRunNode | null {
  for (const node of runs) {
    if (node.level === "sub" && node.status === "running") return node
    const found = findRunningSubRun(node.children)
    if (found) return found
  }
  return null
}

/** The finding category a reviewer role reports under. */
function findingCategory(role: string): string {
  return role.replace("-reviewer", "").replace("-", "_")
}

/** One plausible finding for a live review, drawn from the tree's own pools. */
function liveFinding(
  seed: number,
  role: string,
): Record<string, unknown> {
  return {
    path: FINDING_PATHS[seed % FINDING_PATHS.length],
    line: 20 + (seed % 400),
    severity: "high",
    category: findingCategory(role),
    message: FINDING_MESSAGES[seed % FINDING_MESSAGES.length],
    suggestion: "Guard the call and surface the failure to the caller.",
    confidence: 0.62 + ((seed % 30) / 100),
  }
}

/**
 * The frames a running session's stream delivers, in order.
 *
 * One script per session, chosen from what its tree still has running:
 *
 * - a target is mid-review: its running sub-agent steps once more, reports a
 *   finding, and completes;
 * - a target is still queued: its PR orchestrator starts and runs one reviewer;
 * - every target is settled but the session is not: the main orchestrator runs
 *   the cross-PR synthesis pass as a sub-agent of its own (spec §11.1).
 */
function buildLiveFrames(
  session: ReviewSession,
  main: AgentRunNode,
  events: Map<string, AgentEventItem[]>,
): LiveFrame[] {
  if (session.status !== "running") return []

  const frames: LiveFrame[] = []
  let clockMs = latestEventMs(events)
  // Sequence numbers reserved for runs whose events are not in the log yet, so
  // a script numbers a run the same way the log would once it delivers them.
  const pendingSeq = new Map<string, number>()

  const at = (): string => iso((clockMs += LIVE_FRAME_INTERVAL_MS))

  const push = (
    runId: string,
    parentRunId: string | null,
    type: string,
    payload: Record<string, unknown>,
    createdAt: string,
    spawned: AgentRunNode | null = null,
  ): void => {
    const seq = (pendingSeq.get(runId) ?? events.get(runId)?.length ?? 0) + 1
    pendingSeq.set(runId, seq)
    frames.push({
      event: eventWith(runId, parentRunId, seq, type, payload, createdAt),
      spawned,
      applied: false,
    })
  }

  /** A sub-agent `parent` just spawned, from its spawn to its completion. */
  const pushSubAgent = (
    parent: AgentRunNode,
    target: SessionTarget | null,
    role: string,
    objective: string,
  ): void => {
    const id = `${parent.id}_sub${parent.children.filter((child) => child.level === "sub").length}`
    const seed = hash(`${id}:live`)
    const tokens = 1_200 + (seed % 2_400)
    const spawnedAt = at()
    push(
      id,
      parent.id,
      "agent.spawned",
      {
        role,
        level: "sub",
        parent_run_id: parent.id,
        target_id: target?.id ?? null,
        objective,
        model_role: "review",
        depth: parent.level === "main" ? 1 : 2,
      },
      spawnedAt,
      {
        id,
        sessionId: session.id,
        targetId: target?.id ?? null,
        parentRunId: parent.id,
        level: "sub",
        role,
        modelId: null,
        objective,
        status: "running",
        tokens: 0,
        costUsd: 0,
        startedAt: spawnedAt,
        endedAt: null,
        error: null,
        children: [],
      },
    )
    push(
      id,
      parent.id,
      "agent.started",
      { role, model_id: session.model, status: "running" },
      at(),
    )
    push(
      id,
      parent.id,
      "agent.step",
      {
        step: 1,
        model_id: session.model,
        summary: target
          ? `Reading the changed files for ${target.repository.fullName}#${target.number}.`
          : "Reading every target's findings before it reports.",
        tool_call_count: 1,
      },
      at(),
    )
    push(id, parent.id, "agent.finding", liveFinding(seed, role), at())
    push(
      id,
      parent.id,
      "agent.completed",
      {
        status: "done",
        summary: "Returned 1 finding for its orchestrator to merge",
        tokens_used: tokens,
        step_count: 1,
        cost_usd: costOf(session, tokens),
        finding_count: 1,
      },
      at(),
    )
  }

  const runningSub = findRunningSubRun(main.children)
  if (runningSub) {
    const target =
      session.targets.find((row) => row.id === runningSub.targetId) ?? null
    const parentId = runningSub.parentRunId ?? main.id
    const findingCount =
      (events.get(runningSub.id) ?? []).filter(
        (row) => row.type === "agent.finding",
      ).length + 1
    push(
      runningSub.id,
      parentId,
      "agent.step",
      {
        step: 2,
        model_id: session.model,
        summary: target
          ? `Cross-checking the finding against #${target.number}'s call sites.`
          : "Cross-checking the finding against the changed call sites.",
        tool_call_count: 1,
      },
      at(),
    )
    push(
      runningSub.id,
      parentId,
      "agent.finding",
      liveFinding(hash(`${runningSub.id}:live`), runningSub.role),
      at(),
    )
    push(
      runningSub.id,
      parentId,
      "agent.completed",
      {
        status: "done",
        summary: `Returned ${findingCount} findings for the PR orchestrator to merge`,
        tokens_used: runningSub.tokens,
        step_count: 2,
        cost_usd: runningSub.costUsd,
        finding_count: findingCount,
      },
      at(),
    )
    push(
      parentId,
      main.id,
      "agent.message",
      {
        role: "assistant",
        summary: "Merging the sub-agent findings before reporting up.",
        chars: 48,
      },
      at(),
    )
    return frames
  }

  const queued = session.targets.find((target) => target.status === "queued")
  if (queued) {
    const prId = `run_${session.id}_${queued.id}`
    const objective = `Review PR #${queued.number}: ${queued.title}`
    const role = SUB_ROLES[hash(`${session.id}:${queued.id}`) % SUB_ROLES.length]
    const spawnedAt = at()
    const prNode: AgentRunNode = {
      id: prId,
      sessionId: session.id,
      targetId: queued.id,
      parentRunId: main.id,
      level: "pr",
      role: "orchestrator.pr",
      modelId: null,
      objective,
      status: "running",
      tokens: 0,
      costUsd: 0,
      startedAt: spawnedAt,
      endedAt: null,
      error: null,
      children: [],
    }
    push(
      prId,
      main.id,
      "agent.spawned",
      {
        role: "orchestrator.pr",
        level: "pr",
        parent_run_id: main.id,
        target_id: queued.id,
        objective,
        model_role: "harness.orchestrator",
        depth: 1,
      },
      spawnedAt,
      prNode,
    )
    push(
      prId,
      main.id,
      "agent.started",
      { role: "orchestrator.pr", model_id: session.model, status: "running" },
      at(),
    )
    pushSubAgent(
      prNode,
      queued,
      role,
      SUB_OBJECTIVES[role]?.(queued) ?? `Review #${queued.number}`,
    )
    return frames
  }

  pushSubAgent(
    main,
    null,
    "reviewer",
    "Re-check the merged findings across every target before the session reports",
  )
  return frames
}

function scenarioOf(request: Request): string {
  return request.headers.get("x-mock-scenario") ?? "default"
}

async function latency(request: Request): Promise<void> {
  if (scenarioOf(request) === "slow") {
    await delay(2200)
    return
  }
  await delay(320 + Math.floor(Math.random() * 420))
}

function errorResponse(
  status: number,
  code: string,
  message: string,
  detail?: string,
): Response {
  const body = { error: { code, message, detail } }
  return HttpResponse.json(body, { status })
}

function findSession(id: unknown): ReviewSession | undefined {
  return dataset.sessions.find((session) => session.id === id)
}

export const runsHandlers = [
  http.get(`${API_BASE}/sessions/:id/runs/tree`, async ({ request, params }) => {
    await latency(request)
    if (scenarioOf(request) === "error") {
      return errorResponse(
        500,
        "run_tree_unavailable",
        "Could not load the agent run tree.",
      )
    }
    const session = findSession(params.id)
    if (!session) {
      return errorResponse(
        404,
        "session_not_found",
        "That review session does not exist.",
        `No session with id "${String(params.id)}".`,
      )
    }
    const body: AgentRunTreeResponse = {
      runs: scenarioOf(request) === "empty" ? [] : sessionRuns(session).runs,
    }
    return HttpResponse.json(body)
  }),

  http.get(
    `${API_BASE}/sessions/:id/runs/:runId/events`,
    async ({ request, params }) => {
      await latency(request)
      if (scenarioOf(request) === "error") {
        return errorResponse(
          500,
          "run_events_unavailable",
          "Could not load this run's events.",
        )
      }
      const session = findSession(params.id)
      if (!session) {
        return errorResponse(
          404,
          "session_not_found",
          "That review session does not exist.",
          `No session with id "${String(params.id)}".`,
        )
      }
      const rows = sessionRuns(session).events.get(String(params.runId))
      if (!rows) {
        return errorResponse(
          404,
          "run_not_found",
          "That run is not part of this session.",
          `Run "${String(params.runId)}" has no events in session "${session.id}".`,
        )
      }

      const url = new URL(request.url)
      const afterSeq = Number(url.searchParams.get("afterSeq") ?? "0") || 0
      const limit = Math.min(
        1000,
        Math.max(1, Number(url.searchParams.get("limit") ?? "200") || 200),
      )
      const items =
        scenarioOf(request) === "empty"
          ? []
          : rows.filter((row) => row.seq > afterSeq).slice(0, limit)
      const body: AgentEventPage = {
        items,
        nextSeq: items.length > 0 ? items[items.length - 1].seq : null,
      }
      return HttpResponse.json(body)
    },
  ),
]
