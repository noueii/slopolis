import { HttpResponse, delay, http } from "msw"

import type { UserRef } from "@/api/contract"
import { USERS } from "./data"

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "/api"

export const MOCK_ROLE_STORAGE_KEY = "slopolis:mock-role"

const [ADMIN_ACCOUNT] = USERS

const DEFAULT_USER: UserRef = {
  id: `usr_${ADMIN_ACCOUNT.handle}`,
  handle: ADMIN_ACCOUNT.handle,
  name: ADMIN_ACCOUNT.name,
  avatarUrl: undefined,
  isAdmin: true,
}

function nonAdminRequested(): boolean {
  if (typeof window === "undefined") return false
  try {
    if (window.localStorage.getItem(MOCK_ROLE_STORAGE_KEY) === "user") {
      return true
    }
    return new URLSearchParams(window.location.search).get("mockRole") === "user"
  } catch {
    return false
  }
}

export const meHandlers = [
  http.get(`${API_BASE}/me`, async () => {
    await delay(120)
    const user = nonAdminRequested()
      ? { ...DEFAULT_USER, isAdmin: false }
      : DEFAULT_USER
    return HttpResponse.json(user)
  }),
]
