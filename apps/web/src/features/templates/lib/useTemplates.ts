/**
 * Data hooks for review templates. They call `src/api/client.ts` only, so
 * swapping MSW for the real backend needs no change in the UI layer.
 */

import { api } from "@/api/client"
import type {
  ReviewTemplate,
  ReviewTemplateListResponse,
} from "@/api/contract"
import { useAsync, type AsyncResult } from "@/features/sessions/lib/useSessions"

export function useTemplates(): AsyncResult<ReviewTemplateListResponse> {
  return useAsync(() => api.listTemplates(), "templates")
}

export function useTemplate(id: string | null): AsyncResult<ReviewTemplate> {
  return useAsync(
    () =>
      id
        ? api.getTemplate(id)
        : Promise.reject(new Error("No template selected")),
    id ?? "__none__",
  )
}
