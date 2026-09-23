/**
 * Review presets (`GET /api/presets`) for the dock's preset selector. Calls
 * `src/api/client.ts` only, so swapping MSW for the real backend needs no
 * change here.
 */

import { api } from "@/api/client"
import type { ReviewPresetCatalog } from "@/api/contract"
import { useAsync, type AsyncResult } from "@/features/sessions/lib/useSessions"

export function usePresets(): AsyncResult<ReviewPresetCatalog> {
  return useAsync(() => api.listPresets(), "presets")
}
