/**
 * Data hooks for the Sessions feature.
 *
 * These call `src/api/client.ts` only. Swapping mocks for the real backend
 * requires no changes here or in any component.
 */

import { useCallback, useEffect, useMemo, useState } from "react"

import { ApiError, api } from "@/api/client"
import type {
  Paginated,
  ReviewSession,
  SessionFilterOptions,
  SessionListParams,
  SessionStats,
} from "@/api/contract"

export interface AsyncState<T> {
  data: T | null
  status: "loading" | "success" | "error"
  error: string | null
}

export interface AsyncResult<T> extends AsyncState<T> {
  refetch: () => void
}

export function useAsync<T>(loader: () => Promise<T>, key: string): AsyncResult<T> {
  const [state, setState] = useState<AsyncState<T>>({
    data: null,
    status: "loading",
    error: null,
  })
  const [nonce, setNonce] = useState(0)

  useEffect(() => {
    let cancelled = false
    setState((prev) => ({ data: prev.data, status: "loading", error: null }))
    loader()
      .then((data) => {
        if (!cancelled) setState({ data, status: "success", error: null })
      })
      .catch((error: unknown) => {
        if (cancelled) return
        const message =
          error instanceof ApiError
            ? error.message
            : "Something went wrong while loading sessions."
        setState({ data: null, status: "error", error: message })
      })
    return () => {
      cancelled = true
    }
    // `loader` is intentionally omitted: `key` fully describes the request.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, nonce])

  const refetch = useCallback(() => setNonce((n) => n + 1), [])
  return { ...state, refetch }
}

export function useSessions(params: SessionListParams): AsyncResult<Paginated<ReviewSession>> {
  const key = useMemo(() => JSON.stringify(params), [params])
  return useAsync(() => api.listSessions(params), key)
}

export function useSession(id: string | null): AsyncResult<ReviewSession> {
  return useAsync(
    () =>
      id
        ? api.getSession(id)
        : Promise.reject(new Error("No session selected")),
    id ?? "__none__",
  )
}

export function useSessionFilterOptions(): AsyncResult<SessionFilterOptions> {
  return useAsync(() => api.getFilterOptions(), "filter-options")
}

export function useSessionStats(): AsyncResult<SessionStats> {
  return useAsync(() => api.getSessionStats(), "session-stats")
}

export function useDebouncedValue<T>(value: T, delay = 250): T {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), delay)
    return () => window.clearTimeout(timer)
  }, [value, delay])
  return debounced
}
