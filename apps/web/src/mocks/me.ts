/**
 * MSW handlers for the signed-in account (`/api/me`).
 *
 * The mock role picks which account is reported. `admin` (default) and `user`
 * are members of `MOCK_WORKSPACE`; `no-workspace` reproduces a freshly signed-in
 * account that still has to create or join one. A workspace created through
 * `POST /api/workspaces` is remembered here, so the next `/me` read closes the
 * onboarding gate — exactly like the real backend would.
 */

import { HttpResponse, delay, http } from "msw"

import type { MeResponse, WorkspaceRef } from "@/api/contract"
import { USERS } from "./data"

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "/api"

export const MOCK_ROLE_STORAGE_KEY = "slopolis:mock-role"

/** Roles the mock `/me` can report, set via `?mockRole=` or localStorage. */
export type MockRole = "admin" | "user" | "no-workspace"

export const MOCK_WORKSPACE: WorkspaceRef = {
  id: "ws_acme_labs",
  name: "acme-labs",
  slug: "acme-labs",
}

const [ADMIN_ACCOUNT] = USERS

let createdWorkspace: WorkspaceRef | null = null

export function requestedMockRole(): MockRole {
  if (typeof window === "undefined") return "admin"
  let requested: string | null = null
  try {
    requested =
      window.localStorage.getItem(MOCK_ROLE_STORAGE_KEY) ??
      new URLSearchParams(window.location.search).get("mockRole")
  } catch {
    requested = null
  }
  return requested === "user" || requested === "no-workspace"
    ? requested
    : "admin"
}

/** The workspace the mock account belongs to, or `null` before it has one. */
export function currentWorkspace(): WorkspaceRef | null {
  return requestedMockRole() === "no-workspace"
    ? createdWorkspace
    : MOCK_WORKSPACE
}

export function rememberCreatedWorkspace(workspace: WorkspaceRef): void {
  createdWorkspace = workspace
}

export const meHandlers = [
  http.get(`${API_BASE}/me`, async () => {
    await delay(120)
    const role = requestedMockRole()
    const user: MeResponse = {
      id: `usr_${ADMIN_ACCOUNT.handle}`,
      handle: ADMIN_ACCOUNT.handle,
      name: ADMIN_ACCOUNT.name,
      avatarUrl: undefined,
      isAdmin: role === "admin",
      workspace: currentWorkspace(),
    }
    return HttpResponse.json(user)
  }),
]
