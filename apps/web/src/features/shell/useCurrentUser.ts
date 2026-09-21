import { useCallback, useEffect, useRef, useState } from "react"

import { api, clearSignInAttempt } from "@/api/client"
import type { MeResponse, WorkspaceRef } from "@/api/contract"

export interface CurrentUser {
  user: MeResponse | null
  /** The workspace the account belongs to; `null` until it creates or joins one. */
  workspace: WorkspaceRef | null
  isAdmin: boolean
  isLoading: boolean
  /** Re-reads `/api/me`, resolving once the fresh account has been applied. */
  refresh: () => Promise<void>
}

interface CurrentUserState {
  user: MeResponse | null
  workspace: WorkspaceRef | null
  isAdmin: boolean
  isLoading: boolean
}

const GUEST: CurrentUserState = {
  user: null,
  workspace: null,
  isAdmin: false,
  isLoading: false,
}

export function useCurrentUser(): CurrentUser {
  const [state, setState] = useState<CurrentUserState>({
    user: null,
    workspace: null,
    isAdmin: false,
    isLoading: true,
  })
  const mounted = useRef(true)

  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
    }
  }, [])

  const refresh = useCallback(async () => {
    try {
      const user = await api.getMe()
      // A session landed, so this tab is no longer "already tried": signing out
      // later can send the browser to GitHub again.
      clearSignInAttempt()
      if (!mounted.current) return
      setState({
        user,
        workspace: user.workspace,
        isAdmin: user.isAdmin,
        isLoading: false,
      })
    } catch {
      // A failed `/me` is the signed-out state: the sign-in gate takes over.
      if (mounted.current) setState(GUEST)
    }
  }, [])

  useEffect(() => {
    // Re-reads keep the account on screen instead of flashing a loading state.
    setState((prev) => ({ ...prev, isLoading: prev.user === null }))
    void refresh()
  }, [refresh])

  return { ...state, refresh }
}
