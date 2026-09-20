/**
 * MSW handlers for `/api/workspaces`: membership, and the workspace's caps.
 *
 * Workspace membership itself lives in `./me` — `/me` is what the onboarding
 * gate reads — so creating one here just records it there, mirroring the real
 * backend where the next `/api/me` reports the new workspace.
 *
 * The caps (spec 10.10) are held in a module-level store, so a change survives
 * a reload the way it would against the real API, and the PATCH mirrors the
 * server's edge validation: a field must be `null` or an integer >= 1, an
 * unknown field is refused, and an omitted field keeps its current value.
 * Empty/error/slow are driven by the `x-mock-scenario` header the API client
 * attaches.
 */

import { HttpResponse, delay, http } from "msw"

import type {
  ApiErrorBody,
  CreateWorkspaceRequest,
  WorkspaceListResponse,
  WorkspaceRef,
  WorkspaceSettings,
  WorkspaceSettingsUpdate,
} from "@/api/contract"
import { currentWorkspace, rememberCreatedWorkspace } from "./me"

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "/api"

/** The caps the settings route accepts, wire-named like the server schema. */
const CAP_KEYS = [
  "maxConcurrentSessions",
  "maxSessionsPerUserPerDay",
  "maxTargetsPerRepo",
  "maxTargetsPerInstallation",
] as const

type CapKey = (typeof CAP_KEYS)[number]

/**
 * A workspace that has never set a cap: every field `null`, i.e. unlimited.
 * This is also what the empty scenario serves, since "no caps" is what the
 * screen shows before an admin makes a decision.
 */
function createSettingsStore(): WorkspaceSettings {
  return {
    maxConcurrentSessions: null,
    maxSessionsPerUserPerDay: null,
    maxTargetsPerRepo: null,
    maxTargetsPerInstallation: null,
  }
}

let settingsStore = createSettingsStore()

/** Restore the seed, so a test starts from the same deterministic store. */
export function resetWorkspaceSettingsStore(): void {
  settingsStore = createSettingsStore()
}

function scenarioOf(request: Request): string {
  return request.headers.get("x-mock-scenario") ?? "default"
}

async function latency(request: Request): Promise<void> {
  if (scenarioOf(request) === "slow") {
    await delay(2200)
    return
  }
  await delay(220 + Math.floor(Math.random() * 260))
}

function errorResponse(
  status: number,
  code: string,
  message: string,
  detail?: string,
): Response {
  const body: ApiErrorBody = { error: { code, message, detail } }
  return HttpResponse.json(body, { status })
}

/** The envelope the server renders a rejected body inside. */
function validationResponse(detail: string): Response {
  return errorResponse(
    422,
    "validation_error",
    "The request body or query parameters were invalid.",
    detail,
  )
}

/**
 * Read one cap out of a PATCH body the way the server's schema does: `null`
 * clears the cap, an integer >= 1 sets it, and anything else — a fraction, a
 * string, zero, a negative — is refused with the field named.
 */
function readCap(key: string, value: unknown): number | null | Response {
  if (value === null) return null
  if (typeof value !== "number" || !Number.isInteger(value)) {
    return validationResponse(`${key}: Input should be a valid integer`)
  }
  if (value < 1) {
    return validationResponse(
      `${key}: Input should be greater than or equal to 1`,
    )
  }
  return value
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

  http.get(`${API_BASE}/workspaces/settings`, async ({ request }) => {
    await latency(request)
    if (scenarioOf(request) === "error") {
      return errorResponse(
        500,
        "settings_unavailable",
        "Could not load the workspace settings.",
        "The workspace settings query did not respond.",
      )
    }

    return HttpResponse.json(
      scenarioOf(request) === "empty" ? createSettingsStore() : settingsStore,
    )
  }),

  http.patch(`${API_BASE}/workspaces/settings`, async ({ request }) => {
    await latency(request)
    if (scenarioOf(request) === "error") {
      return errorResponse(
        500,
        "settings_update_failed",
        "Could not save the workspace settings.",
      )
    }

    const payload = (await request.json()) as Record<string, unknown>
    const patch: WorkspaceSettingsUpdate = {}
    // The schema forbids unknown fields, so a typo in a cap name is refused
    // rather than silently ignored.
    for (const [key, value] of Object.entries(payload)) {
      if (!(CAP_KEYS as readonly string[]).includes(key)) {
        return validationResponse(`${key}: Extra inputs are not permitted`)
      }
      const cap = readCap(key, value)
      if (cap instanceof Response) return cap
      patch[key as CapKey] = cap
    }

    // An omitted key keeps its value while an explicit `null` clears the cap:
    // the merge is what makes the body partial rather than a replacement.
    settingsStore = { ...settingsStore, ...patch }
    return HttpResponse.json(settingsStore)
  }),
]
