/**
 * Typed Server-Sent Events client for one review session.
 *
 * The server (`GET /api/sessions/{id}/events`) streams a lightweight status
 * projection until the session reaches a terminal state, then emits a final
 * `done` event and closes. `subscribeToSession` normalizes both event names into
 * one typed callback and returns an unsubscribe function.
 */

import type { SessionStatus, TargetStatus } from "./contract"

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
 * Subscribe to a session's status stream. Returns a teardown function that
 * closes the connection and detaches every listener.
 */
export function subscribeToSession(
  id: string,
  { onEvent, onError }: SessionEventHandlers,
): () => void {
  const source = new EventSource(
    `${API_BASE}/sessions/${encodeURIComponent(id)}/events`,
  )

  const handleMessage = (event: Event) => {
    const payload = parsePayload((event as MessageEvent<string>).data)
    if (payload) onEvent(payload)
  }

  const handleDone = (event: Event) => {
    const payload = parsePayload((event as MessageEvent<string>).data)
    if (payload) onEvent(payload)
    source.close()
  }

  source.addEventListener("session", handleMessage)
  source.addEventListener("done", handleDone)
  source.onerror = (event) => onError(event)

  return () => {
    source.removeEventListener("session", handleMessage)
    source.removeEventListener("done", handleDone)
    source.onerror = null
    source.close()
  }
}
