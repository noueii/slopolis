/**
 * Data hook for the Usage feature (spec 10.9).
 *
 * It calls `src/api/client.ts` only, so replacing the mock API with the real
 * backend changes nothing here or in the components.
 */

import { api } from "@/api/client"
import type { UsageResponse } from "@/api/contract"
import {
  useAsync,
  type AsyncResult,
} from "@/features/sessions/lib/useSessions"

export function useUsage(): AsyncResult<UsageResponse> {
  return useAsync(() => api.getUsage(), "usage")
}
