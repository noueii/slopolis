import { useEffect, useState } from "react"

import { api } from "@/api/client"
import type { UserRef } from "@/api/contract"

export interface CurrentUser {
  user: UserRef | null
  isAdmin: boolean
  isLoading: boolean
}

const GUEST: CurrentUser = { user: null, isAdmin: false, isLoading: false }

export function useCurrentUser(): CurrentUser {
  const [state, setState] = useState<CurrentUser>({
    user: null,
    isAdmin: false,
    isLoading: true,
  })

  useEffect(() => {
    let cancelled = false
    api
      .getMe()
      .then((user) => {
        if (!cancelled) {
          setState({ user, isAdmin: user.isAdmin, isLoading: false })
        }
      })
      .catch(() => {
        if (!cancelled) setState(GUEST)
      })
    return () => {
      cancelled = true
    }
  }, [])

  return state
}
