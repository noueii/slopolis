/**
 * MSW handlers for provider credentials, the model catalog, and role → model
 * assignments (spec 10.2, mock layer only).
 *
 * Serves the shapes in `src/api/contract.ts` from a module-level mutable store,
 * so a created credential, an import, or a role change survives for the browser
 * session the way it would against the real API. The server's semantics are
 * mirrored deliberately:
 *
 * - a key is sealed and only its last four characters are ever returned;
 * - a connection test records its outcome on the row and answers 200 even when
 *   the provider is unreachable — a failure is a status, not an API error;
 * - import counts newly created rows only and refreshes models already there;
 * - `auto` is the absence of an assignment row, so the role reports a null id;
 * - the catalog's first row is the model `auto` resolves to;
 * - deleting a credential cascades to the rows imported through it and clears
 *   the assignments pointing at removed models.
 *
 * Empty/error/slow are driven by the `x-mock-scenario` header the API client
 * attaches.
 */

import { HttpResponse, delay, http } from "msw"

import type {
  ApiErrorBody,
  AssignmentResponse,
  CatalogModel,
  CatalogModelInput,
  CatalogModelListResponse,
  ModelImportRequest,
  ModelImportResponse,
  ProviderCredential,
  ProviderInput,
  ProviderListResponse,
  ProviderTestResult,
  ProviderUpdate,
  RoleAssignment,
} from "@/api/contract"

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "/api"

/** Every role a workspace may bind to a model, in the display order the API uses. */
const ASSIGNABLE_ROLES = [
  "review",
  "harness.orchestrator",
  "review.fast",
  "review.specialist",
  "review.security",
  "review.tests",
] as const

/** The seed binds only the v1 reviewer; every harness role stays `auto`. */
const SEEDED_REVIEW_ROLE = "review"

/**
 * What each provider family answers from `/v1/models`. The mock has no provider
 * to call, so the probe reads this table: a family that is not listed here is
 * the "provider unreachable" case, reported as a recorded status rather than an
 * invented success.
 */
const PROVIDER_MODEL_IDS: Record<string, readonly string[]> = {
  litellm: ["claude-sonnet-4", "claude-opus-4", "gpt-4o-mini"],
  openai: ["gpt-4o", "gpt-4o-mini", "o4-mini"],
  anthropic: ["claude-sonnet-4", "claude-opus-4"],
  google: ["gemini-2.5-pro", "gemini-2.5-flash"],
}

/** A credential as the vault holds it — `apiKey` never leaves this module. */
interface StoredCredential {
  id: string
  provider: string
  baseUrl: string | null
  apiKey: string
  enabled: boolean
  lastStatus: "ok" | "failed" | null
  lastCheckedAt: string | null
  createdAt: string
}

interface ProviderStore {
  credentials: StoredCredential[]
  /** Catalog rows in the server's `(created_at, model_id)` order; first is default. */
  models: CatalogModel[]
  /** role → model id; a role with no entry is `auto`. */
  assignments: Map<string, string>
  /** Feeds ids for created rows, so each is distinct and addressable. */
  seq: number
}

const DAY_MS = 24 * 60 * 60 * 1000

/** The deterministic seed: one gateway credential, an imported and a manual model. */
function createProviderStore(now = Date.now()): ProviderStore {
  const credential: StoredCredential = {
    id: "cred_litellm",
    provider: "litellm",
    baseUrl: "https://litellm.acme-labs.dev",
    apiKey: "sk-litellm-prod-9f2c",
    enabled: true,
    lastStatus: "ok",
    lastCheckedAt: new Date(now - 2 * DAY_MS).toISOString(),
    createdAt: new Date(now - 12 * DAY_MS).toISOString(),
  }
  const reviewModel = "claude-sonnet-4"
  return {
    credentials: [credential],
    models: [
      {
        id: "model_claude_sonnet",
        modelId: reviewModel,
        provider: credential.provider,
        displayName: null,
        source: "import",
        credentialId: credential.id,
      },
      {
        id: "model_gpt4o_mini",
        modelId: "gpt-4o-mini",
        provider: "openai",
        displayName: "GPT-4o mini",
        source: "manual",
        credentialId: null,
      },
    ],
    assignments: new Map([[SEEDED_REVIEW_ROLE, reviewModel]]),
    seq: 0,
  }
}

let providerStore = createProviderStore()

/** Restore the seed, so a test starts from the same deterministic store. */
export function resetProviderStore(now?: number): void {
  providerStore = createProviderStore(now)
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

/** What the API is allowed to say about a sealed key. */
function maskCredential(row: StoredCredential): ProviderCredential {
  return {
    id: row.id,
    provider: row.provider,
    baseUrl: row.baseUrl,
    keyLast4: row.apiKey.slice(-4),
    enabled: row.enabled,
    lastStatus: row.lastStatus,
    lastCheckedAt: row.lastCheckedAt,
    createdAt: row.createdAt,
  }
}

/** Trim an optional base URL; a blank one means the provider's own default. */
function cleanBaseUrl(value: string | null | undefined): string | null {
  return value?.trim().replace(/\/+$/, "") || null
}

/**
 * The mock's stand-in for the server's `/v1/models` probe. A family the mock
 * has no model list for is the unreachable case, reported as a detail rather
 * than thrown, exactly as the server reports it.
 */
function probeProvider(
  provider: string,
): { ids: readonly string[]; detail: null } | { ids: null; detail: string } {
  const ids = PROVIDER_MODEL_IDS[provider.toLowerCase()]
  if (ids === undefined) {
    return {
      ids: null,
      detail: `HTTP 404: no model list for provider "${provider}".`,
    }
  }
  return { ids, detail: null }
}

function nextId(store: ProviderStore, prefix: string): string {
  store.seq += 1
  return `${prefix}_${store.seq}`
}

function credentialNotFound(): Response {
  return errorResponse(
    404,
    "credential_not_found",
    "That provider credential is not in this workspace.",
  )
}

/** Drop the assignments pointing at models that no longer exist. */
function clearAssignments(removedModelIds: readonly string[]): void {
  if (removedModelIds.length === 0) return
  const removed = new Set(removedModelIds)
  for (const [role, modelId] of providerStore.assignments) {
    if (removed.has(modelId)) providerStore.assignments.delete(role)
  }
}

/** The workspace default plus one entry per assignable role; missing rows are `auto`. */
function roleAssignments(
  models: readonly CatalogModel[],
  assigned: ReadonlyMap<string, string>,
): AssignmentResponse {
  const known = new Set(models.map((model) => model.modelId))
  return {
    defaultModelId: models[0]?.modelId ?? null,
    roles: ASSIGNABLE_ROLES.map((role) => {
      const modelId = assigned.get(role)
      return {
        role,
        modelId: modelId !== undefined && known.has(modelId) ? modelId : null,
      }
    }),
  }
}

export const providersHandlers = [
  http.get(`${API_BASE}/providers`, async ({ request }) => {
    await latency(request)
    if (scenarioOf(request) === "error") {
      return errorResponse(
        500,
        "providers_unavailable",
        "Could not load provider credentials.",
        "The workspace vault did not respond.",
      )
    }

    const body: ProviderListResponse = {
      items:
        scenarioOf(request) === "empty"
          ? []
          : providerStore.credentials.map(maskCredential),
    }
    return HttpResponse.json(body)
  }),

  http.post(`${API_BASE}/providers`, async ({ request }) => {
    await latency(request)
    if (scenarioOf(request) === "error") {
      return errorResponse(
        500,
        "provider_create_failed",
        "Could not save the provider credential.",
      )
    }

    const input = (await request.json()) as Partial<ProviderInput>
    const provider = input.provider?.trim() ?? ""
    if (!provider) {
      return errorResponse(422, "provider_required", "Provide a provider name.")
    }
    const apiKey = input.apiKey?.trim() ?? ""
    if (!apiKey) {
      return errorResponse(422, "api_key_required", "Provide an API key.")
    }

    const row: StoredCredential = {
      id: nextId(providerStore, "cred"),
      provider,
      baseUrl: cleanBaseUrl(input.baseUrl),
      apiKey,
      enabled: true,
      lastStatus: null,
      lastCheckedAt: null,
      createdAt: new Date().toISOString(),
    }
    providerStore.credentials.push(row)
    return HttpResponse.json(maskCredential(row), { status: 201 })
  }),

  http.patch(`${API_BASE}/providers/:id`, async ({ request, params }) => {
    await latency(request)
    if (scenarioOf(request) === "error") {
      return errorResponse(
        500,
        "provider_update_failed",
        "Could not save the provider credential.",
      )
    }

    const row = providerStore.credentials.find(
      (credential) => credential.id === params.id,
    )
    if (row === undefined) return credentialNotFound()

    const patch = (await request.json()) as Partial<ProviderUpdate>
    // An omitted base URL and an explicit `null` both leave the row alone; the
    // UI clears one by submitting an empty string.
    if (patch.baseUrl != null) row.baseUrl = cleanBaseUrl(patch.baseUrl)
    if (patch.enabled !== undefined) row.enabled = patch.enabled
    if (patch.apiKey !== undefined) {
      const apiKey = patch.apiKey.trim()
      if (!apiKey) {
        return errorResponse(422, "api_key_required", "Provide an API key.")
      }
      row.apiKey = apiKey
    }
    return HttpResponse.json(maskCredential(row))
  }),

  http.delete(`${API_BASE}/providers/:id`, async ({ request, params }) => {
    await latency(request)
    if (scenarioOf(request) === "error") {
      return errorResponse(
        500,
        "provider_delete_failed",
        "Could not delete the provider credential.",
      )
    }

    const index = providerStore.credentials.findIndex(
      (credential) => credential.id === params.id,
    )
    if (index < 0) return credentialNotFound()
    const [row] = providerStore.credentials.splice(index, 1)

    // The rows imported through the credential go with it; manual rows survive.
    const removedModelIds = providerStore.models
      .filter((model) => model.credentialId === row.id)
      .map((model) => model.modelId)
    providerStore.models = providerStore.models.filter(
      (model) => model.credentialId !== row.id,
    )
    clearAssignments(removedModelIds)
    return new HttpResponse(null, { status: 204 })
  }),

  http.post(`${API_BASE}/providers/:id/test`, async ({ request, params }) => {
    await latency(request)
    if (scenarioOf(request) === "error") {
      return errorResponse(
        500,
        "provider_test_failed",
        "The connection test could not be run.",
      )
    }

    const row = providerStore.credentials.find(
      (credential) => credential.id === params.id,
    )
    if (row === undefined) return credentialNotFound()

    const probe = probeProvider(row.provider)
    const status: ProviderTestResult["status"] = probe.detail === null ? "ok" : "failed"
    const checkedAt = new Date().toISOString()
    row.lastStatus = status
    row.lastCheckedAt = checkedAt
    return HttpResponse.json({ status, detail: probe.detail, checkedAt })
  }),

  http.get(`${API_BASE}/catalog/models`, async ({ request }) => {
    await latency(request)
    if (scenarioOf(request) === "error") {
      return errorResponse(
        500,
        "catalog_unavailable",
        "Could not load the model catalog.",
      )
    }

    const body: CatalogModelListResponse =
      scenarioOf(request) === "empty"
        ? { items: [], defaultModelId: null }
        : {
            items: providerStore.models.map((model) => ({ ...model })),
            defaultModelId: providerStore.models[0]?.modelId ?? null,
          }
    return HttpResponse.json(body)
  }),

  http.post(`${API_BASE}/catalog/models`, async ({ request }) => {
    await latency(request)
    if (scenarioOf(request) === "error") {
      return errorResponse(
        500,
        "model_create_failed",
        "Could not add the model.",
      )
    }

    const input = (await request.json()) as Partial<CatalogModelInput>
    const modelId = input.modelId?.trim() ?? ""
    if (!modelId) {
      return errorResponse(422, "model_id_required", "Provide a model id.")
    }
    const provider = input.provider?.trim() ?? ""
    if (!provider) {
      return errorResponse(422, "provider_required", "Provide a provider name.")
    }
    if (providerStore.models.some((model) => model.modelId === modelId)) {
      return errorResponse(
        409,
        "model_exists",
        "That model is already in the workspace catalog.",
      )
    }

    const row: CatalogModel = {
      id: nextId(providerStore, "model"),
      modelId,
      provider,
      displayName: input.displayName?.trim() || null,
      source: "manual",
      credentialId: null,
    }
    providerStore.models.push(row)
    return HttpResponse.json({ ...row }, { status: 201 })
  }),

  http.post(`${API_BASE}/catalog/models/import`, async ({ request }) => {
    await latency(request)
    if (scenarioOf(request) === "error") {
      return errorResponse(
        500,
        "model_import_failed",
        "Could not import models from the provider.",
      )
    }

    const input = (await request.json()) as Partial<ModelImportRequest>
    const credential = providerStore.credentials.find(
      (row) => row.id === input.credentialId,
    )
    if (credential === undefined) return credentialNotFound()

    const probe = probeProvider(credential.provider)
    if (probe.ids === null) {
      return errorResponse(502, "provider_unavailable", probe.detail)
    }

    let imported = 0
    const items: CatalogModel[] = []
    for (const modelId of probe.ids) {
      const existing = providerStore.models.find(
        (model) => model.modelId === modelId,
      )
      if (existing !== undefined) {
        // Already known: refresh which credential serves it, never import it twice.
        existing.provider = credential.provider
        existing.credentialId = credential.id
        items.push({ ...existing })
        continue
      }
      const row: CatalogModel = {
        id: nextId(providerStore, "model"),
        modelId,
        provider: credential.provider,
        displayName: null,
        source: "import",
        credentialId: credential.id,
      }
      providerStore.models.push(row)
      imported += 1
      items.push({ ...row })
    }

    const body: ModelImportResponse = { imported, items }
    return HttpResponse.json(body)
  }),

  http.delete(`${API_BASE}/catalog/models/:id`, async ({ request, params }) => {
    await latency(request)
    if (scenarioOf(request) === "error") {
      return errorResponse(
        500,
        "model_delete_failed",
        "Could not delete the model.",
      )
    }

    const index = providerStore.models.findIndex((model) => model.id === params.id)
    if (index < 0) {
      return errorResponse(
        404,
        "model_not_found",
        "That model is not in the workspace catalog.",
      )
    }
    const [row] = providerStore.models.splice(index, 1)
    clearAssignments([row.modelId])
    return new HttpResponse(null, { status: 204 })
  }),

  http.get(`${API_BASE}/catalog/assignments`, async ({ request }) => {
    await latency(request)
    if (scenarioOf(request) === "error") {
      return errorResponse(
        500,
        "assignments_unavailable",
        "Could not load role assignments.",
      )
    }

    return HttpResponse.json(
      scenarioOf(request) === "empty"
        ? roleAssignments([], new Map())
        : roleAssignments(providerStore.models, providerStore.assignments),
    )
  }),

  http.put(`${API_BASE}/catalog/assignments/:role`, async ({ request, params }) => {
    await latency(request)
    if (scenarioOf(request) === "error") {
      return errorResponse(
        500,
        "assignment_update_failed",
        "Could not save the role assignment.",
      )
    }

    const role = String(params.role)
    if (!(ASSIGNABLE_ROLES as readonly string[]).includes(role)) {
      return errorResponse(
        422,
        "unknown_role",
        `'${role}' is not an assignable model role.`,
      )
    }

    const body = (await request.json()) as Partial<RoleAssignment>
    const requested = (body.modelId ?? "").trim()
    if (!requested || requested === "auto") {
      // `auto` is the absence of a row, not a stored sentinel.
      providerStore.assignments.delete(role)
      const assignment: RoleAssignment = { role, modelId: null }
      return HttpResponse.json(assignment)
    }

    const model = providerStore.models.find((row) => row.modelId === requested)
    if (model === undefined) {
      return errorResponse(
        422,
        "unknown_model",
        "Assign a model the workspace has imported or added.",
      )
    }
    providerStore.assignments.set(role, model.modelId)
    const assignment: RoleAssignment = { role, modelId: model.modelId }
    return HttpResponse.json(assignment)
  }),
]
