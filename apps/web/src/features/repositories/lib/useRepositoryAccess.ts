import { useCallback, useState } from "react"

import { ApiError, api } from "@/api/client"
import type { RepositorySummary, RepositoryUpdate } from "@/api/contract"

export interface RepositoryAccessMutation {
  pending: boolean
  error: string | null
  /**
   * Applies a repository's own switches (spec 10.1 / 10.10): parking it and the
   * review access rule it triggers under. `null` means the call failed.
   */
  run: (
    repository: RepositorySummary,
    update: RepositoryUpdate,
  ) => Promise<RepositorySummary | null>
}

/**
 * The workspace's per-repository switches, one call at a time. Callers refresh
 * their own data on success; a failure is kept here so the control that started
 * it can explain itself instead of swallowing the reason.
 */
export function useRepositoryAccess(): RepositoryAccessMutation {
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const run = useCallback(
    async (repository: RepositorySummary, update: RepositoryUpdate) => {
      setPending(true)
      setError(null)
      try {
        return await api.updateRepository(repository.id, update)
      } catch (cause) {
        if (cause instanceof ApiError) {
          setError(cause.message)
        } else if (update.enabled !== undefined) {
          setError(
            `Could not ${update.enabled ? "enable" : "disable"} ${repository.fullName}.`,
          )
        } else {
          setError(`Could not save the review access rule for ${repository.fullName}.`)
        }
        return null
      } finally {
        setPending(false)
      }
    },
    [],
  )

  return { pending, error, run }
}
