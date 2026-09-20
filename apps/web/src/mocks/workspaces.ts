/**
 * MSW handlers for `/api/workspaces`.
 *
 * Workspace membership itself lives in `./me` — `/me` is what the onboarding
 * gate reads — so creating one here just records it there, mirroring the real
 * backend where the next `/api/me` reports the new workspace.
 */

import { HttpResponse, delay, http } from "msw"

import type {
  ApiErrorBody,
  CreateWorkspaceRequest,
  WorkspaceListResponse,
  WorkspaceRef,
} from "@/api/contract"
import { currentWorkspace, rememberCreatedWorkspace } from "./me"

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "/api"

function errorResponse(status: number, code: string, message: string): Response {
  const body: ApiErrorBody = { error: { code, message } }
  return HttpResponse.json(body, { status })
}

export const workspaceHandlers = [
  http.get(`${API_BASE}/workspaces`, async () => {
    await delay(160)
    const workspace = currentWorkspace()
    const body: WorkspaceListResponse = { items: workspace ? [workspace] : [] }
    return HttpResponse.json(body)
  }),

  http.post(`${API_BASE}/workspaces`, async ({ request }) => {
    await delay(320)
    const payload = (await request.json()) as CreateWorkspaceRequest | null
    const name = payload?.name?.trim() ?? ""

    if (name.length === 0) {
      return errorResponse(422, "name_required", "Enter a workspace name.")
    }
    if (currentWorkspace()) {
      return errorResponse(
        409,
        "already_in_workspace",
        "This account already belongs to a workspace.",
      )
    }

    const slug =
      name
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, "-")
        .replace(/^-+|-+$/g, "") || "workspace"
    const workspace: WorkspaceRef = { id: `ws_${slug}`, name, slug }
    rememberCreatedWorkspace(workspace)
    return HttpResponse.json(workspace, { status: 201 })
  }),
]
