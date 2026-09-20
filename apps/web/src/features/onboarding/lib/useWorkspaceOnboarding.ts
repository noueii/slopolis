/**
 * Onboarding state for an account that does not belong to a workspace yet.
 *
 * Reads belong to `useCurrentUser` (`/api/me` is the single source of truth for
 * the account and its workspace); this hook owns the one write, `POST
 * /api/workspaces`, plus the re-check that lets the gate close.
 */

import { useCallback, useState } from "react"

import { ApiError, api } from "@/api/client"
import type { WorkspaceRef } from "@/api/contract"
import type { CurrentUser } from "@/features/shell/useCurrentUser"

export interface WorkspaceOnboardingController {
  /** The onboarding request in flight, if any. */
  status: "idle" | "creating" | "checking"
  /** The account's workspace; the gate closes as soon as `/me` reports one. */
  workspace: WorkspaceRef | null
  /** Creates a workspace, then re-reads `/me` so the app can enter the shell. */
  create: (name: string) => void
  /** Re-reads `/me` to pick up an invitation a workspace admin has sent. */
  refresh: () => void
  /** Message for the last failed action, if any. */
  error: string | null
  isCreating: boolean
}

export function useWorkspaceOnboarding(
  currentUser: CurrentUser,
): WorkspaceOnboardingController {
  const [status, setStatus] = useState<"idle" | "creating" | "checking">("idle")
  const [error, setError] = useState<string | null>(null)
  const reload = currentUser.refresh

  const create = useCallback(
    (rawName: string) => {
      const name = rawName.trim()
      if (name.length === 0) {
        setError("Enter a workspace name.")
        return
      }

      setError(null)
      setStatus("creating")
      api
        .createWorkspace(name)
        // Stays `creating` until the fresh `/me` lands, so the form cannot be
        // submitted twice while the gate is closing.
        .then(() => reload())
        .catch((cause: unknown) => {
          setError(
            cause instanceof ApiError
              ? cause.message
              : "Could not create the workspace. Try again.",
          )
          // A 409 means the account has a workspace after all (an admin invited
          // it, or another tab created one); re-reading `/me` closes the gate.
          if (cause instanceof ApiError && cause.code === "already_in_workspace") {
            void reload()
          }
        })
        .finally(() => setStatus("idle"))
    },
    [reload],
  )

  const refresh = useCallback(() => {
    setError(null)
    setStatus("checking")
    void reload().finally(() => setStatus("idle"))
  }, [reload])

  return {
    status,
    workspace: currentUser.workspace,
    create,
    refresh,
    error,
    isCreating: status === "creating",
  }
}
