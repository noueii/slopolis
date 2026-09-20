import { useCallback, useState } from "react"

import { ApiError, api } from "@/api/client"
import type { RepositorySummary } from "@/api/contract"

export interface RepositoryAccessMutation {
  pending: boolean
  error: string | null
  /** Parks or re-enables one repository; `null` means the toggle failed. */
  run: (
    repository: RepositorySummary,
    enabled: boolean,
  ) => Promise<RepositorySummary | null>
}

/**
 * The workspace's enable switch (spec 10.1), one toggle at a time. Callers
 * refresh their own data on success; a failure is kept here so the control that
 * started it can explain itself instead of swallowing the reason.
 */
export function useRepositoryAccess(): RepositoryAccessMutation {
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const run = useCallback(
    async (repository: RepositorySummary, enabled: boolean) => {
      setPending(true)
      setError(null)
      try {
        return await api.updateRepository(repository.id, { enabled })
      } catch (cause) {
        setError(
          cause instanceof ApiError
            ? cause.message
            : `Could not ${enabled ? "enable" : "disable"} ${repository.fullName}.`,
        )
        return null
      } finally {
        setPending(false)
      }
    },
    [],
  )

  return { pending, error, run }
}
