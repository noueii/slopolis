/**
 * Data hooks for the Dashboard. They call `src/api/client.ts` only; swapping
 * MSW for the real backend needs no change here.
 */

import { useCallback, useEffect, useMemo, useState } from "react"

import { ApiError, api } from "@/api/client"
import type {
  DashboardData,
  DashboardParams,
  ModelCatalog,
  RepositoryListResponse,
  RepositoryPullRequestsResponse,
  ReviewPresetCatalog,
} from "@/api/contract"
import {
  useAsync,
  type AsyncResult,
  type AsyncState,
} from "@/features/sessions/lib/useSessions"

export function useRepositories(): AsyncResult<RepositoryListResponse> {
  return useAsync(() => api.listRepositories(), "repositories")
}

export function useDashboard(
  params: DashboardParams,
): AsyncResult<DashboardData> {
  const key = useMemo(() => JSON.stringify(params), [params])
  return useAsync(() => api.getDashboard(params), `dashboard:${key}`)
}

export function useModels(): AsyncResult<ModelCatalog> {
  return useAsync(() => api.listModels(), "models")
}

export function usePresets(): AsyncResult<ReviewPresetCatalog> {
  return useAsync(() => api.listPresets(), "presets")
}

const pullRequestCache = new Map<string, RepositoryPullRequestsResponse>()

/** Invalidates cached PR lists, e.g. when the mock scenario changes. */
export function clearPullRequestCache(): void {
  pullRequestCache.clear()
}

/**
 * Open pull requests for one repository. Results are cached per `owner/name`
 * so switching back and forth between repositories is instant and selections
 * survive the round-trip.
 */
export function useRepositoryPullRequests(
  fullName: string | null,
): AsyncResult<RepositoryPullRequestsResponse> {
  const [state, setState] = useState<AsyncState<RepositoryPullRequestsResponse>>(
    () => {
      const cached = fullName ? pullRequestCache.get(fullName) : undefined
      return cached
        ? { data: cached, status: "success", error: null }
        : { data: null, status: "loading", error: null }
    },
  )
  const [nonce, setNonce] = useState(0)

  useEffect(() => {
    if (!fullName) {
      setState({ data: null, status: "success", error: null })
      return
    }

    const cached = pullRequestCache.get(fullName)
    if (cached) {
      setState({ data: cached, status: "success", error: null })
      return
    }

    let cancelled = false
    setState({ data: null, status: "loading", error: null })
    api
      .listRepositoryPullRequests(fullName)
      .then((data) => {
        if (cancelled) return
        pullRequestCache.set(fullName, data)
        setState({ data, status: "success", error: null })
      })
      .catch((error: unknown) => {
        if (cancelled) return
        setState({
          data: null,
          status: "error",
          error:
            error instanceof ApiError
              ? error.message
              : "Could not load open pull requests.",
        })
      })

    return () => {
      cancelled = true
    }
  }, [fullName, nonce])

  const refetch = useCallback(() => {
    if (fullName) pullRequestCache.delete(fullName)
    setNonce((value) => value + 1)
  }, [fullName])

  return { ...state, refetch }
}
