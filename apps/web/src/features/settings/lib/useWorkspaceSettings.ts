/**
 * Data hooks for the workspace Settings screen (spec 10.10).
 *
 * They call `src/api/client.ts` only, so replacing the mock API with the real
 * backend changes nothing here or in the components.
 */

import { useCallback, useRef, useState } from "react"

import { ApiError, api } from "@/api/client"
import type { WorkspaceSettings, WorkspaceSettingsUpdate } from "@/api/contract"
import {
  useAsync,
  type AsyncResult,
} from "@/features/sessions/lib/useSessions"

/** The API's own message when it explains a refusal, else a local fallback. */
function settingsError(cause: unknown, fallback: string): string {
  if (cause instanceof ApiError) return cause.message
  if (cause instanceof Error && cause.message !== "") return cause.message
  return fallback
}

/**
 * `useAsync` keeps a single message and its fallback names sessions, so a read
 * that fails with anything but an `ApiError` (a dropped connection, say) would
 * explain itself in another feature's words. The raw rejection is kept beside
 * the state and mapped on render, which is the same shape the provider admin
 * resources take.
 */
function useSettingsResource<T>(
  loader: () => Promise<T>,
  key: string,
  fallback: string,
): AsyncResult<T> {
  const failure = useRef<unknown>(null)
  const state = useAsync(() => {
    failure.current = null
    return loader().catch((cause: unknown) => {
      failure.current = cause
      throw cause
    })
  }, key)

  if (state.status !== "error") return state
  return { ...state, error: settingsError(failure.current, fallback) }
}

/** The workspace's caps and queue limits; admin-only on the server. */
export function useWorkspaceSettings(): AsyncResult<WorkspaceSettings> {
  return useSettingsResource(
    () => api.getWorkspaceSettings(),
    "workspace-settings",
    "Something went wrong while loading the workspace settings.",
  )
}

export interface SettingsMutation {
  pending: boolean
  /** The server's refusal for the last write, if it had one. */
  error: string | null
  /** Set from a landed write until the form is touched again. */
  saved: boolean
  /** Resolves `true` when the write landed; `false` leaves `error` set. */
  save: (patch: WorkspaceSettingsUpdate) => Promise<boolean>
  /** Drop the last outcome, e.g. because the admin edited a field. */
  reset: () => void
}

/**
 * The save path: a landed write is re-read so the form shows what the server
 * actually stored (a cap the API normalised, or one another tab changed), and a
 * refusal keeps the server's own words — it is the only side that knows which
 * cap was rejected or whether the caller is still a workspace admin.
 */
export function useWorkspaceSettingsMutation(
  onSaved: () => void,
): SettingsMutation {
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)

  const save = useCallback(
    async (patch: WorkspaceSettingsUpdate) => {
      setPending(true)
      setError(null)
      setSaved(false)
      try {
        await api.updateWorkspaceSettings(patch)
        setSaved(true)
        onSaved()
        return true
      } catch (cause) {
        setError(settingsError(cause, "Could not save the workspace settings."))
        return false
      } finally {
        setPending(false)
      }
    },
    [onSaved],
  )

  const reset = useCallback(() => {
    setSaved(false)
    setError(null)
  }, [])

  return { pending, error, saved, save, reset }
}
