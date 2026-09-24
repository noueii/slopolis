/**
 * The inbox data hook (spec v3 §1/§6).
 *
 * It calls `src/api/client.ts` only, and its cache key is the serialized
 * params, so a filter change is a new request rather than a stale page: the
 * whole screen — rows, backlog totals and filter counts — comes back in one
 * response.
 */

import { useMemo } from "react"

import { api } from "@/api/client"
import type {
  PullRequestListParams,
  PullRequestListResponse,
} from "@/api/contract"
import { useAsync, type AsyncResult } from "@/features/sessions/lib/useSessions"

export function usePullRequests(
  params: PullRequestListParams,
): AsyncResult<PullRequestListResponse> {
  const key = useMemo(() => JSON.stringify(params), [params])
  return useAsync(() => api.listPullRequests(params), key)
}
