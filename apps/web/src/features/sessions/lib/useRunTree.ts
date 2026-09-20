/**
 * Live harness run tree for one session (spec v2 §7).
 *
 * The tree is read once from `GET /sessions/{id}/runs/tree`, then kept current
 * by folding the `agent` events the caller forwards from the session stream.
 * Once the session itself is terminal the persisted log is complete, so the
 * hook re-reads exactly once and settles on what the worker stored — folded
 * events are a fast path, the database is the truth.
 */

import { useCallback, useEffect, useRef, useState } from "react"

import { ApiError, api } from "@/api/client"
import type { AgentEventItem, AgentRunNode, SessionStatus } from "@/api/contract"
import { TERMINAL_SESSION_STATUSES } from "@/api/events"
import { mergeAgentEvent } from "./runTree"

/** How many live events the hook keeps for the node detail pane. */
const LIVE_EVENT_LIMIT = 500

export type RunTreeStatus = "loading" | "success" | "error"

export interface RunTreeResult {
  runs: AgentRunNode[]
  status: RunTreeStatus
  error: string | null
  /** Live events, newest last, bounded; the detail pane folds these in. */
  events: AgentEventItem[]
  /** Load the tree again from the API. */
  refetch: () => void
  /** Fold one streamed harness event onto the tree. */
  applyEvent: (event: AgentEventItem) => void
}

export function useRunTree(
  sessionId: string | null,
  sessionStatus?: SessionStatus,
): RunTreeResult {
  const [runs, setRuns] = useState<AgentRunNode[]>([])
  const [status, setStatus] = useState<RunTreeStatus>("loading")
  const [error, setError] = useState<string | null>(null)
  const [events, setEvents] = useState<AgentEventItem[]>([])
  const [nonce, setNonce] = useState(0)
  const settled = useRef<{ sessionId: string | null; done: boolean }>({
    sessionId: null,
    done: false,
  })

  useEffect(() => {
    if (!sessionId) {
      setRuns([])
      setStatus("success")
      setError(null)
      return
    }
    if (settled.current.sessionId !== sessionId) {
      settled.current = { sessionId, done: false }
      setEvents([])
    }

    let cancelled = false
    setStatus("loading")
    setError(null)
    api
      .getRunTree(sessionId)
      .then((tree) => {
        if (cancelled) return
        setRuns(tree.runs)
        setStatus("success")
      })
      .catch((cause: unknown) => {
        if (cancelled) return
        setError(
          cause instanceof ApiError
            ? cause.message
            : "Could not load the agent run tree.",
        )
        setStatus("error")
      })
    return () => {
      cancelled = true
    }
  }, [sessionId, nonce])

  // One re-read per session, on the transition into a terminal status: the
  // worker is done writing, so the stored tree wins over the folded stream.
  useEffect(() => {
    if (!sessionId || !sessionStatus) return
    if (!TERMINAL_SESSION_STATUSES.has(sessionStatus)) return
    if (settled.current.sessionId !== sessionId || settled.current.done) return
    settled.current = { sessionId, done: true }
    setNonce((value) => value + 1)
  }, [sessionId, sessionStatus])

  const applyEvent = useCallback(
    (event: AgentEventItem) => {
      if (!sessionId) return
      setEvents((prev) =>
        prev.length >= LIVE_EVENT_LIMIT
          ? [...prev.slice(prev.length - LIVE_EVENT_LIMIT + 1), event]
          : [...prev, event],
      )
      setRuns((prev) => mergeAgentEvent(prev, event, sessionId))
    },
    [sessionId],
  )

  const refetch = useCallback(() => setNonce((value) => value + 1), [])

  return { runs, status, error, events, refetch, applyEvent }
}
