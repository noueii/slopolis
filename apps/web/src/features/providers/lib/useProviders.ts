/**
 * Data hooks for the Providers & models screen (spec 10.2).
 *
 * They call `src/api/client.ts` only, so swapping the mock API for the real
 * backend changes nothing here or in the components.
 */

import { useCallback, useRef, useState } from "react"

import { api } from "@/api/client"
import type {
  AssignmentResponse,
  CatalogModelListResponse,
  ProviderListResponse,
} from "@/api/contract"
import {
  useAsync,
  type AsyncResult,
} from "@/features/sessions/lib/useSessions"
import { adminErrorMessage } from "./errors"

/**
 * `useAsync` keeps only a message, but this surface is refused with codes
 * (`admin_required`, `vault_not_configured`) that need their own copy, so the
 * raw rejection is kept beside the state and mapped on render. One hook per
 * endpoint still gives the screen the `{data, status, error, refetch}` shape.
 */
function useAdminResource<T>(
  loader: () => Promise<T>,
  key: string,
  fallback: string,
): AsyncResult<T> {
  const failure = useRef<unknown>(null)
  const state = useAsync(() => {
    failure.current = null
    return loader().catch((error: unknown) => {
      failure.current = error
      throw error
    })
  }, key)

  if (state.status !== "error") return state
  return { ...state, error: adminErrorMessage(failure.current, fallback) }
}

/** Credentials the workspace holds; the key itself is never part of a row. */
export function useProviders(): AsyncResult<ProviderListResponse> {
  return useAdminResource(
    () => api.listProviders(),
    "providers",
    "Something went wrong while loading provider credentials.",
  )
}

/** The workspace model catalog plus the model `auto` resolves to. */
export function useCatalog(): AsyncResult<CatalogModelListResponse> {
  return useAdminResource(
    () => api.listCatalogModels(),
    "catalog-models",
    "Something went wrong while loading the model catalog.",
  )
}

/** Every assignable role with its model, `null` meaning `auto`. */
export function useAssignments(): AsyncResult<AssignmentResponse> {
  return useAdminResource(
    () => api.getAssignments(),
    "catalog-assignments",
    "Something went wrong while loading role assignments.",
  )
}

export interface AdminMutation {
  pending: boolean
  error: string | null
  /** Resolves `true` when the mutation landed; `false` leaves `error` set. */
  run: (action: () => Promise<void>, fallback: string) => Promise<boolean>
  clearError: () => void
}

/**
 * One in-flight admin mutation: every write on this screen has to show the
 * same pending/refused pair, and a refusal has to be explained rather than
 * thrown away by the dialog that triggered it.
 */
export function useAdminMutation(): AdminMutation {
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const run = useCallback(
    async (action: () => Promise<void>, fallback: string) => {
      setPending(true)
      setError(null)
      try {
        await action()
        return true
      } catch (cause) {
        setError(adminErrorMessage(cause, fallback))
        return false
      } finally {
        setPending(false)
      }
    },
    [],
  )

  const clearError = useCallback(() => setError(null), [])

  return { pending, error, run, clearError }
}
