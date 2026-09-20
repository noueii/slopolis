/**
 * Pure helpers for the harness run tree (spec v2 §7).
 *
 * The tree is loaded once from `GET /sessions/{id}/runs/tree` and then kept
 * current by folding the `agent` events the session stream carries onto it. A
 * run already in the tree is patched in place; a run the tree has not seen yet
 * is inserted under its announced parent, so a live spawn appears without a
 * refetch. Everything here is immutable so React can diff the result.
 */

import type {
  AgentEventItem,
  AgentRunLevel,
  AgentRunNode,
  AgentRunStatus,
} from "@/api/contract"

export const RUN_STATUS_LABELS: Record<AgentRunStatus, string> = {
  pending: "Pending",
  running: "Running",
  done: "Done",
  failed: "Failed",
  cancelled: "Cancelled",
}

const TERMINAL_RUN_STATUSES: Record<AgentRunStatus, boolean> = {
  pending: false,
  running: false,
  done: true,
  failed: true,
  cancelled: true,
}

export function isTerminalRunStatus(status: AgentRunStatus): boolean {
  return TERMINAL_RUN_STATUSES[status]
}

/**
 * The status one event announces, or `null` for an event that only reports
 * progress (`agent.tool_result`) or a run the tree has yet to see.
 *
 * `agent.spawned` maps to `running` rather than `pending`: a spawn is followed
 * immediately by `agent.started` in practice, and a run that exists but has not
 * started is still a live child of a live orchestrator.
 */
const EVENT_STATUS: Record<string, AgentRunStatus> = {
  "agent.spawned": "running",
  "agent.started": "running",
  "agent.step": "running",
  "agent.tool_call": "running",
  "agent.tool_result": "running",
  "agent.message": "running",
  "agent.finding": "running",
  "agent.completed": "done",
  "agent.failed": "failed",
  "agent.cancelled": "cancelled",
}

function compareRuns(a: AgentRunNode, b: AgentRunNode): number {
  if (a.startedAt !== b.startedAt) {
    if (a.startedAt === null) return 1
    if (b.startedAt === null) return -1
    return a.startedAt < b.startedAt ? -1 : 1
  }
  return a.id < b.id ? -1 : a.id > b.id ? 1 : 0
}

/** Flatten a tree depth-first, in the same order the tree pane renders it. */
export function flattenRuns(
  runs: AgentRunNode[],
  depth = 0,
): Array<{ node: AgentRunNode; depth: number }> {
  const flat: Array<{ node: AgentRunNode; depth: number }> = []
  for (const node of runs) {
    flat.push({ node, depth })
    flat.push(...flattenRuns(node.children, depth + 1))
  }
  return flat
}

export function countRuns(runs: AgentRunNode[]): number {
  return flattenRuns(runs).length
}

export function findRun(
  runs: AgentRunNode[],
  runId: string,
): AgentRunNode | null {
  for (const node of runs) {
    if (node.id === runId) return node
    const found = findRun(node.children, runId)
    if (found) return found
  }
  return null
}

function numberField(payload: Record<string, unknown>, key: string): number | null {
  const value = payload[key]
  return typeof value === "number" && Number.isFinite(value) ? value : null
}

function stringField(payload: Record<string, unknown>, key: string): string | null {
  const value = payload[key]
  return typeof value === "string" && value.length > 0 ? value : null
}

function levelField(value: string | null): AgentRunLevel {
  return value === "main" || value === "pr" || value === "sub" ? value : "sub"
}

/** Build the node an `agent.spawned` event describes, for a run not yet listed. */
function nodeFromSpawn(event: AgentEventItem, sessionId: string): AgentRunNode {
  return {
    id: event.runId,
    sessionId,
    targetId: stringField(event.payload, "target_id"),
    parentRunId: event.parentRunId,
    level: levelField(stringField(event.payload, "level")),
    role: stringField(event.payload, "role") ?? "agent",
    modelId: null,
    objective: stringField(event.payload, "objective") ?? "",
    status: "running",
    tokens: 0,
    costUsd: 0,
    startedAt: event.createdAt,
    endedAt: null,
    error: null,
    children: [],
  }
}

/** Replace the node `event.runId` names, or `null` when the tree lacks it. */
function patchById(
  nodes: AgentRunNode[],
  event: AgentEventItem,
): AgentRunNode[] | null {
  let patched = false
  const next = nodes.map((node) => {
    if (node.id === event.runId) {
      patched = true
      return patchRun(node, event)
    }
    const children = patchById(node.children, event)
    if (!children) return node
    patched = true
    return { ...node, children }
  })
  return patched ? next : null
}

/** Fold one event onto its node, returning the changed node. */
function patchRun(node: AgentRunNode, event: AgentEventItem): AgentRunNode {
  const status = EVENT_STATUS[event.type]
  const endedAt = status && isTerminalRunStatus(status) ? event.createdAt : null

  return {
    ...node,
    status: status ?? node.status,
    modelId: stringField(event.payload, "model_id") ?? node.modelId,
    startedAt: node.startedAt ?? event.createdAt,
    endedAt: endedAt ?? node.endedAt,
    tokens: numberField(event.payload, "tokens_used") ?? node.tokens,
    costUsd: numberField(event.payload, "cost_usd") ?? node.costUsd,
    error: stringField(event.payload, "error") ?? node.error,
  }
}

/** Append `child` under `parentId`, or return the input when it is absent. */
function insertChild(
  nodes: AgentRunNode[],
  parentId: string,
  child: AgentRunNode,
): AgentRunNode[] {
  let inserted = false
  const next = nodes.map((node) => {
    if (inserted) return node
    if (node.id === parentId) {
      inserted = true
      return { ...node, children: [...node.children, child].sort(compareRuns) }
    }
    const children = insertChild(node.children, parentId, child)
    if (children === node.children) return node
    inserted = true
    return { ...node, children }
  })
  return inserted ? next : nodes
}

/** Fold one live harness event onto the tree. */
export function mergeAgentEvent(
  runs: AgentRunNode[],
  event: AgentEventItem,
  sessionId: string,
): AgentRunNode[] {
  const patched = patchById(runs, event)
  if (patched) return patched.sort(compareRuns)
  if (event.type !== "agent.spawned") return runs

  const spawned = nodeFromSpawn(event, sessionId)
  if (event.parentRunId === null) {
    return [...runs, spawned].sort(compareRuns)
  }
  return insertChild(runs, event.parentRunId, spawned)
}

/**
 * Merge a node's loaded event page with the live events seen since, oldest
 * first. Live events are deduped against the page: a re-read after the stream
 * settles overlaps what was already streamed.
 */
export function mergeRunEvents(
  loaded: AgentEventItem[],
  live: AgentEventItem[],
): AgentEventItem[] {
  const byId = new Map<string, AgentEventItem>()
  for (const event of [...loaded, ...live]) byId.set(event.id, event)
  return [...byId.values()].sort((a, b) => a.seq - b.seq)
}
