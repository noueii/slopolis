/**
 * Bridges the session SSE stream into React state with a polling fallback.
 *
 * Live events reach the caller through `onUpdate` (the status projection) and
 * `onAgentEvent` (the harness event log, spec v2 §7). If the stream errors (mock
 * mode, proxy hiccup, or an environment that cannot hold a long-lived
 * connection) the hook closes the stream and polls `GET /api/sessions/{id}`
 * until the session reaches a terminal status, so the detail view still
 * reflects progress.
 */

import { useEffect, useRef, useState } from "react"

import { api } from "@/api/client"
import type { AgentEventItem, SessionStatus, TargetStatus } from "@/api/contract"
import {
  TERMINAL_SESSION_STATUSES,
  subscribeToSession,
  type SessionEventPayload,
} from "@/api/events"

/** How the detail view is currently learning about status changes. */
export type SessionEventsMode = "idle" | "connecting" | "live" | "polling"

/** One status projection, whether it arrived over SSE or polling. */
export interface SessionEventUpdate {
  status: SessionStatus
  targets: Array<{ id: string; status: TargetStatus }>
}

export interface UseSessionEventsOptions {
  /** Called for every status projection; the caller merges it onto its data. */
  onUpdate: (update: SessionEventUpdate) => void
  /**
   * Called for every harness event the stream carries, in arrival order. The
   * polling fallback cannot replay these, so a caller must treat them as
   * incremental on top of what it already loaded.
   */
  onAgentEvent?: (event: AgentEventItem) => void
  /** Set false to stay idle and open no connection. */
  enabled?: boolean
  /**
   * Bump to reopen the stream. A terminal status closes it for good, so when
   * the session is put back in flight (a manual retry) only the caller knows
   * there is another attempt to follow.
   */
  epoch?: number
}

const POLL_INTERVAL_MS = 2500

function toUpdate(payload: SessionEventPayload): SessionEventUpdate {
  return {
    status: payload.status,
    targets: payload.targets.map((target) => ({
      id: target.id,
      status: target.status,
    })),
  }
}

export function useSessionEvents(
  id: string | null,
  { onUpdate, onAgentEvent, enabled = true, epoch = 0 }: UseSessionEventsOptions,
): SessionEventsMode {
  const [mode, setMode] = useState<SessionEventsMode>("idle")
  const onUpdateRef = useRef(onUpdate)
  onUpdateRef.current = onUpdate
  const onAgentEventRef = useRef(onAgentEvent)
  onAgentEventRef.current = onAgentEvent

  useEffect(() => {
    if (!id || !enabled) {
      setMode("idle")
      return
    }

    let finished = false
    let pollTimer: number | null = null
    let teardown: (() => void) | null = null

    const stopPolling = () => {
      if (pollTimer !== null) {
        window.clearInterval(pollTimer)
        pollTimer = null
      }
    }

    const deliver = (update: SessionEventUpdate) => {
      if (finished) return
      onUpdateRef.current(update)
      if (TERMINAL_SESSION_STATUSES.has(update.status)) {
        finished = true
        stopPolling()
      }
    }

    const startPolling = () => {
      if (finished || pollTimer !== null) return
      setMode("polling")
      pollTimer = window.setInterval(() => {
        api
          .getSession(id)
          .then((session) =>
            deliver({
              status: session.status,
              targets: session.targets.map((target) => ({
                id: target.id,
                status: target.status,
              })),
            }),
          )
          .catch(() => undefined)
      }, POLL_INTERVAL_MS)
    }

    setMode("connecting")
    teardown = subscribeToSession(id, {
      onEvent: (payload) => {
        if (finished) return
        stopPolling()
        setMode("live")
        deliver(toUpdate(payload))
      },
      onAgent: (event) => {
        // An agent frame proves the stream is healthy, so it also lifts the
        // view out of polling — but it never ends the stream.
        if (finished) return
        stopPolling()
        setMode("live")
        onAgentEventRef.current?.(event)
      },
      onError: () => {
        if (finished) return
        teardown?.()
        startPolling()
      },
    })

    return () => {
      finished = true
      stopPolling()
      teardown?.()
    }
  }, [id, enabled, epoch])

  return mode
}
