/**
 * The workspace model catalog (`GET /api/models`). Calls `src/api/client.ts`
 * only, so swapping MSW for the real backend needs no change here.
 */

import { api } from "@/api/client"
import type { ModelCatalog } from "@/api/contract"
import { useAsync, type AsyncResult } from "@/features/sessions/lib/useSessions"

export function useModels(): AsyncResult<ModelCatalog> {
  return useAsync(() => api.listModels(), "models")
}
