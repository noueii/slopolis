/**
 * `GET /api/repositories`: every repository the workspace's GitHub App
 * installation covers. Calls `src/api/client.ts` only, so swapping MSW for the
 * real backend needs no change here.
 */

import { api } from "@/api/client"
import type { RepositoryListResponse } from "@/api/contract"
import { useAsync, type AsyncResult } from "@/features/sessions/lib/useSessions"

export function useRepositories(): AsyncResult<RepositoryListResponse> {
  return useAsync(() => api.listRepositories(), "repositories")
}
