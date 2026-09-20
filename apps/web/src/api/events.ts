/**
 * Typed Server-Sent Events client for one review session.
 *
 * The server (`GET /api/sessions/{id}/events`) streams a lightweight status
 * projection until the session reaches a terminal state, then emits a final
 * `done` event and closes. Since Harness V1 it also streams one `agent` event
 * per persisted harness event, drained before `done` (spec v2 §7).
 * `subscribeToSession` normalizes all three event names into typed callbacks
 * and returns an unsubscribe function.
 */

import type { AgentEventItem, SessionStatus, TargetStatus } from "./contract"

const API_BASE = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "/api"

const SESSION_STATUSES: SessionStatus[] = [
  "queued",
  "running",
  "done",
  "failed",
  "cancelled",
]

const TARGET_STATUSES: TargetStatus[] = [
  "queued",
  "running",
  "done",
  "failed",
  "cancelled",
  "skipped",
]

/** Statuses after which the server closes the stream. */
export const TERMINAL_SESSION_STATUSES: ReadonlySet<SessionStatus> = new Set([
  "done",
  "failed",
  "cancelled",
])

/** One target's live status inside a session event. */
export interface SessionEventTarget {
  id: string
  number: number
  status: TargetStatus
}

/** The status projection the server streams for one session. */
export interface SessionEventPayload {
  id: string
  status: SessionStatus
  targets: SessionEventTarget[]
}

export interface SessionEventHandlers {
  onEvent: (payload: SessionEventPayload) => void
  /** One harness event; the stream drains these before `done` (spec v2 §7). */
  onAgent?: (event: AgentEventItem) => void
  onError: (error: Event) => void
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null
}

function isSessionStatus(value: unknown): value is SessionStatus {
  return typeof value === "string" && (SESSION_STATUSES as string[]).includes(value)
}

function isTargetStatus(value: unknown): value is TargetStatus {
  return typeof value === "string" && (TARGET_STATUSES as string[]).includes(value)
}

function parseTarget(value: unknown): SessionEventTarget | null {
  if (!isRecord(value)) return null
  const { id, number, status } = value
  if (typeof id !== "string" || typeof number !== "number" || !isTargetStatus(status)) {
    return null
  }
  return { id, number, status }
}

function parsePayload(raw: string): SessionEventPayload | null {
  let value: unknown
  try {
    value = JSON.parse(raw)
  } catch {
    return null
  }
  if (!isRecord(value)) return null
  const { id, status, targets } = value
  if (typeof id !== "string" || !isSessionStatus(status) || !Array.isArray(targets)) {
    return null
  }

  const parsedTargets: SessionEventTarget[] = []
  for (const rawTarget of targets as unknown[]) {
    const target = parseTarget(rawTarget)
    if (target) parsedTargets.push(target)
  }
  return { id, status, targets: parsedTargets }
}

/**
 * Parse one `agent` frame. A payload that does not carry the fields the run
 * tree merges on is dropped rather than delivered half-formed: the tree keeps
 * its previous state and the next event (or the settle re-read) corrects it.
 */
function parseAgentEvent(raw: string): AgentEventItem | null {
  let value: unknown
  try {
    value = JSON.parse(raw)
  } catch {
    return null
  }
  if (!isRecord(value)) return null
  const { id, runId, parentRunId, seq, type, payload, createdAt } = value
  if (
    typeof id !== "string" ||
    typeof runId !== "string" ||
    typeof seq !== "number" ||
    typeof type !== "string" ||
    typeof createdAt !== "string" ||
    !isRecord(payload)
  ) {
    return null
  }
  return {
    id,
    runId,
    parentRunId: typeof parentRunId === "string" ? parentRunId : null,
    seq,
    type,
    payload,
    createdAt,
  }
}

/**
 * Subscribe to a session's status stream. Returns a teardown function that
 * closes the connection and detaches every listener.
 */
export function subscribeToSession(
  id: string,
  { onEvent, onAgent, onError }: SessionEventHandlers,
): () => void {
  const source = new EventSource(
    `${API_BASE}/sessions/${encodeURIComponent(id)}/events`,
  )

  const handleMessage = (event: Event) => {
    const payload = parsePayload((event as MessageEvent<string>).data)
    if (payload) onEvent(payload)
  }

  const handleAgent = (event: Event) => {
    const agentEvent = parseAgentEvent((event as MessageEvent<string>).data)
    if (agentEvent) onAgent?.(agentEvent)
  }

  const handleDone = (event: Event) => {
    const payload = parsePayload((event as MessageEvent<string>).data)
    if (payload) onEvent(payload)
    source.close()
  }

  source.addEventListener("session", handleMessage)
  source.addEventListener("agent", handleAgent)
  source.addEventListener("done", handleDone)
  source.onerror = (event) => onError(event)

  return () => {
    source.removeEventListener("session", handleMessage)
    source.removeEventListener("agent", handleAgent)
    source.removeEventListener("done", handleDone)
    source.onerror = null
    source.close()
  }
}
