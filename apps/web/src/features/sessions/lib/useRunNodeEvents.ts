/**
 * One run's persisted event log, one page at a time (spec v2 §7).
 *
 * The replay endpoint pages by cursor: a page carries the highest `seq` it
 * returned, and the client passes it back as `afterSeq`. A page that came back
 * full is the only honest signal that more may follow — the cursor is non-null
 * even on the last page, so `hasMore` cannot be read off `nextSeq` alone.
 */

import { useCallback, useEffect, useState } from "react"

import { ApiError, api } from "@/api/client"
import type { AgentEventItem } from "@/api/contract"
import { mergeRunEvents } from "./runTree"

/** The server's default page size; a page this long may have a successor. */
const PAGE_SIZE = 200

export type RunNodeEventsStatus = "idle" | "loading" | "success" | "error"

export interface RunNodeEventsResult {
  items: AgentEventItem[]
  status: RunNodeEventsStatus
  error: string | null
  /** A full page came back, so asking for the next one is worth a click. */
  hasMore: boolean
  loadingMore: boolean
  loadMore: () => void
  reload: () => void
}

export function useRunNodeEvents(
  sessionId: string | null,
  runId: string | null,
): RunNodeEventsResult {
  const [items, setItems] = useState<AgentEventItem[]>([])
  const [status, setStatus] = useState<RunNodeEventsStatus>("idle")
  const [error, setError] = useState<string | null>(null)
  const [cursor, setCursor] = useState<number | null>(null)
  const [hasMore, setHasMore] = useState(false)
  const [loadingMore, setLoadingMore] = useState(false)
  const [nonce, setNonce] = useState(0)

  useEffect(() => {
    if (!sessionId || !runId) {
      setItems([])
      setStatus("idle")
      setError(null)
      setCursor(null)
      setHasMore(false)
      return
    }

    let cancelled = false
    setItems([])
    setStatus("loading")
    setError(null)
    setCursor(null)
    setHasMore(false)
    api
      .getRunEvents(sessionId, runId)
      .then((page) => {
        if (cancelled) return
        setItems(page.items)
        setCursor(page.nextSeq)
        setHasMore(page.items.length >= PAGE_SIZE && page.nextSeq !== null)
        setStatus("success")
      })
      .catch((cause: unknown) => {
        if (cancelled) return
        setError(
          cause instanceof ApiError
            ? cause.message
            : "Could not load this run's events.",
        )
        setStatus("error")
      })
    return () => {
      cancelled = true
    }
  }, [sessionId, runId, nonce])

  const loadMore = useCallback(() => {
    if (!sessionId || !runId || cursor === null || loadingMore) return
    setLoadingMore(true)
    setError(null)
    api
      .getRunEvents(sessionId, runId, { afterSeq: cursor })
      .then((page) => {
        setItems((prev) => mergeRunEvents(prev, page.items))
        setCursor(page.nextSeq)
        setHasMore(page.items.length >= PAGE_SIZE && page.nextSeq !== null)
      })
      .catch((cause: unknown) => {
        setError(
          cause instanceof ApiError
            ? cause.message
            : "Could not load more of this run's events.",
        )
      })
      .finally(() => setLoadingMore(false))
  }, [sessionId, runId, cursor, loadingMore])

  const reload = useCallback(() => setNonce((value) => value + 1), [])

  return { items, status, error, hasMore, loadingMore, loadMore, reload }
}
