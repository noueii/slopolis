/**
 * slopolis API client.
 *
 * UI code talks to the backend exclusively through this module. The fetch
 * layer is mock-agnostic: when MSW is active it intercepts these requests at
 * the network boundary, and when the real FastAPI server exists the same
 * calls hit it unchanged.
 */

import type {
  AgentEventPage,
  AgentRunTreeResponse,
  ApiErrorBody,
  AssignmentResponse,
  CatalogModel,
  CatalogModelInput,
  CatalogModelListResponse,
  CreateReviewRequest,
  CreateWorkspaceRequest,
  CreatedSession,
  DashboardData,
  DashboardParams,
  MeResponse,
  ModelCatalog,
  ModelImportRequest,
  ModelImportResponse,
  Paginated,
  PreflightRequest,
  PreflightResult,
  ProviderCredential,
  ProviderInput,
  ProviderListResponse,
  ProviderTestResult,
  ProviderUpdate,
  RepositoryListResponse,
  RepositoryPullRequestsResponse,
  RepositorySummary,
  ReviewPresetCatalog,
  ReviewSession,
  ReviewTemplate,
  ReviewTemplateInput,
  ReviewTemplateListResponse,
  RoleAssignment,
  SessionFilterOptions,
  SessionListParams,
  SessionStats,
  UsageResponse,
  WorkspaceListResponse,
  WorkspaceRef,
} from "./contract"

const API_BASE = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "/api"

/**
 * Where the GitHub App install flow starts. `/github/install` is a browser
 * navigation, not a JSON route: the API redirects on to GitHub's install page
 * for the App, which is also where repositories are added or removed.
 */
export const githubAppInstallUrl = `${API_BASE}/github/install`

/** Mock-only request modes, toggled from the app's mock-data control. */
export type MockScenario = "default" | "empty" | "error" | "slow"

const SCENARIO_STORAGE_KEY = "slopolis:mock-scenario"
const SCENARIOS: MockScenario[] = ["default", "empty", "error", "slow"]

function readStoredScenario(): MockScenario {
  try {
    const stored = window.localStorage.getItem(SCENARIO_STORAGE_KEY)
    return stored && (SCENARIOS as string[]).includes(stored)
      ? (stored as MockScenario)
      : "default"
  } catch {
    return "default"
  }
}

let mockScenario: MockScenario = readStoredScenario()

export function setMockScenario(next: MockScenario): void {
  mockScenario = next
  try {
    window.localStorage.setItem(SCENARIO_STORAGE_KEY, next)
  } catch {
    return
  }
}

export function getMockScenario(): MockScenario {
  return mockScenario
}

/**
 * Whether the app is talking to the mock API. Controls the mock-data menu:
 * `VITE_MOCK=0` forces it off, `VITE_MOCK=1` forces it on, otherwise it is on
 * in development. The actual mock/real swap happens at the dev proxy
 * (`vite.config.ts`) — see `make dev-mock` / `make dev-api`.
 */
export function isMockModeEnabled(): boolean {
  const mode = import.meta.env.VITE_MOCK
  if (mode === "off") return false
  if (mode === "server" || mode === "worker") return true
  return import.meta.env.DEV
}

/** Remembers that this tab has already been sent to GitHub once. */
const SIGN_IN_ATTEMPT_KEY = "slopolis:signin-attempted"

/** Whether this tab came back from GitHub without a session. */
export function signInAttempted(): boolean {
  try {
    return window.sessionStorage.getItem(SIGN_IN_ATTEMPT_KEY) === "1"
  } catch {
    // Storage can be unavailable (private mode); the gate just loses its guard.
    return false
  }
}

/** Forgets the attempt, so a later signed-out visit can redirect again. */
export function clearSignInAttempt(): void {
  try {
    window.sessionStorage.removeItem(SIGN_IN_ATTEMPT_KEY)
  } catch {
    return
  }
}

export type SignInStart =
  | { started: true }
  | { started: false; message: string }

/**
 * Send the browser to GitHub's consent screen.
 *
 * The flow is probed before navigating: a deployment without OAuth credentials
 * answers the login route with a 503 body, and navigating into that would strand
 * the visitor on raw JSON with no way back to the app.
 */
export async function beginSignIn(): Promise<SignInStart> {
  // `/auth/github/login` is a browser navigation, not a JSON route: the API
  // redirects on to GitHub's consent screen, and the callback returns the
  // browser to `APP_URL` with the session cookie set.
  const url = `${API_BASE}/auth/github/login`
  try {
    const response = await fetch(url, { redirect: "manual" })
    // A startable flow answers with a redirect the script cannot read into
    // (`opaqueredirect`); any readable non-ok status is a refusal.
    if (response.type !== "opaqueredirect" && !response.ok) {
      let body: ApiErrorBody | undefined
      try {
        body = (await response.json()) as ApiErrorBody
      } catch {
        body = undefined
      }
      return {
        started: false,
        message:
          body?.error.message ?? "GitHub sign-in is unavailable right now.",
      }
    }
  } catch {
    // Unreadable response (cross-origin or offline): let the navigation report it.
  }

  try {
    window.sessionStorage.setItem(SIGN_IN_ATTEMPT_KEY, "1")
  } catch {
    // See `signInAttempted`: the guard is best-effort.
  }
  window.location.replace(url)
  return { started: true }
}

export class ApiError extends Error {
  readonly status: number
  readonly code: string

  constructor(status: number, code: string, message: string) {
    super(message)
    this.name = "ApiError"
    this.status = status
    this.code = code
  }
}

function buildQuery(params: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === "") continue
    search.set(key, String(value))
  }
  const qs = search.toString()
  return qs ? `?${qs}` : ""
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers)
  headers.set("Accept", "application/json")
  if (init?.body !== undefined) headers.set("Content-Type", "application/json")
  // Read only by the MSW handlers; omitted against a real backend.
  if (isMockModeEnabled()) headers.set("x-mock-scenario", mockScenario)

  const response = await fetch(`${API_BASE}${path}`, { ...init, headers })

  if (!response.ok) {
    let body: ApiErrorBody | undefined
    try {
      body = (await response.json()) as ApiErrorBody
    } catch {
      body = undefined
    }
    throw new ApiError(
      response.status,
      body?.error.code ?? `http_${response.status}`,
      body?.error.message ?? `Request to ${path} failed (${response.status}).`,
    )
  }

  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

export const api = {
  getMe(): Promise<MeResponse> {
    return request<MeResponse>("/me")
  },

  listWorkspaces(): Promise<WorkspaceListResponse> {
    return request<WorkspaceListResponse>("/workspaces")
  },

  createWorkspace(name: string): Promise<WorkspaceRef> {
    const body: CreateWorkspaceRequest = { name }
    return request<WorkspaceRef>("/workspaces", {
      method: "POST",
      body: JSON.stringify(body),
    })
  },

  listSessions(params: SessionListParams = {}): Promise<Paginated<ReviewSession>> {
    const query = buildQuery({
      q: params.q?.trim() || undefined,
      repo: params.repo || undefined,
      user: params.user || undefined,
      status: params.status || undefined,
      range: params.range && params.range !== "all" ? params.range : undefined,
      page: params.page ?? 1,
      pageSize: params.pageSize ?? 25,
      sort: params.sort ?? "created_desc",
    })
    return request<Paginated<ReviewSession>>(`/sessions${query}`)
  },

  getSession(id: string): Promise<ReviewSession> {
    return request<ReviewSession>(`/sessions/${encodeURIComponent(id)}`)
  },

  getFilterOptions(): Promise<SessionFilterOptions> {
    return request<SessionFilterOptions>("/sessions/filters")
  },

  getSessionStats(): Promise<SessionStats> {
    return request<SessionStats>("/sessions/stats")
  },

  listRepositories(): Promise<RepositoryListResponse> {
    return request<RepositoryListResponse>("/repositories")
  },

  /**
   * Park or re-enable a repository (spec 10.1). A parked repository keeps its
   * sessions and findings and is refused at pre-flight.
   */
  updateRepository(
    repositoryId: string,
    body: { enabled: boolean },
  ): Promise<RepositorySummary> {
    return request<RepositorySummary>(
      `/repositories/${encodeURIComponent(repositoryId)}`,
      { method: "PATCH", body: JSON.stringify(body) },
    )
  },

  listRepositoryPullRequests(
    fullName: string,
  ): Promise<RepositoryPullRequestsResponse> {
    const [owner, name] = fullName.split("/")
    return request<RepositoryPullRequestsResponse>(
      `/repositories/${encodeURIComponent(owner ?? "")}/${encodeURIComponent(name ?? "")}/pulls`,
    )
  },

  listModels(): Promise<ModelCatalog> {
    return request<ModelCatalog>("/models")
  },

  listPresets(): Promise<ReviewPresetCatalog> {
    return request<ReviewPresetCatalog>("/presets")
  },

  listTemplates(): Promise<ReviewTemplateListResponse> {
    return request<ReviewTemplateListResponse>("/templates")
  },

  getTemplate(id: string): Promise<ReviewTemplate> {
    return request<ReviewTemplate>(`/templates/${encodeURIComponent(id)}`)
  },

  createTemplate(body: ReviewTemplateInput): Promise<ReviewTemplate> {
    return request<ReviewTemplate>("/templates", {
      method: "POST",
      body: JSON.stringify(body),
    })
  },

  updateTemplate(
    id: string,
    body: Partial<ReviewTemplateInput>,
  ): Promise<ReviewTemplate> {
    return request<ReviewTemplate>(`/templates/${encodeURIComponent(id)}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    })
  },

  getDashboard(params: DashboardParams = {}): Promise<DashboardData> {
    const query = buildQuery({
      repo: params.repo || undefined,
      limit: params.limit,
    })
    return request<DashboardData>(`/dashboard${query}`)
  },

  preflightReview(body: PreflightRequest): Promise<PreflightResult> {
    return request<PreflightResult>("/reviews/preflight", {
      method: "POST",
      body: JSON.stringify(body),
    })
  },

  createReviewSession(body: CreateReviewRequest): Promise<CreatedSession> {
    return request<CreatedSession>("/sessions", {
      method: "POST",
      body: JSON.stringify(body),
    })
  },

  // --- provider & model administration (spec 10.2) --------------------------

  listProviders(): Promise<ProviderListResponse> {
    return request<ProviderListResponse>("/providers")
  },

  createProvider(input: ProviderInput): Promise<ProviderCredential> {
    return request<ProviderCredential>("/providers", {
      method: "POST",
      body: JSON.stringify(input),
    })
  },

  updateProvider(
    id: string,
    patch: ProviderUpdate,
  ): Promise<ProviderCredential> {
    return request<ProviderCredential>(`/providers/${encodeURIComponent(id)}`, {
      method: "PATCH",
      body: JSON.stringify(patch),
    })
  },

  deleteProvider(id: string): Promise<void> {
    return request<void>(`/providers/${encodeURIComponent(id)}`, {
      method: "DELETE",
    })
  },

  testProvider(id: string): Promise<ProviderTestResult> {
    // The provider being down comes back as `status: "failed"` with 200; only
    // the request itself failing (missing credential, no vault) throws.
    return request<ProviderTestResult>(
      `/providers/${encodeURIComponent(id)}/test`,
      { method: "POST" },
    )
  },

  listCatalogModels(): Promise<CatalogModelListResponse> {
    return request<CatalogModelListResponse>("/catalog/models")
  },

  addCatalogModel(input: CatalogModelInput): Promise<CatalogModel> {
    return request<CatalogModel>("/catalog/models", {
      method: "POST",
      body: JSON.stringify(input),
    })
  },

  importCatalogModels(credentialId: string): Promise<ModelImportResponse> {
    const body: ModelImportRequest = { credentialId }
    return request<ModelImportResponse>("/catalog/models/import", {
      method: "POST",
      body: JSON.stringify(body),
    })
  },

  deleteCatalogModel(id: string): Promise<void> {
    // Addressed by catalog row id: model ids contain slashes and would not
    // survive as a path segment.
    return request<void>(`/catalog/models/${encodeURIComponent(id)}`, {
      method: "DELETE",
    })
  },

  getAssignments(): Promise<AssignmentResponse> {
    return request<AssignmentResponse>("/catalog/assignments")
  },

  setAssignment(
    role: string,
    modelId: string | null,
  ): Promise<RoleAssignment> {
    // `null` is `auto`: the server deletes the assignment row rather than
    // storing a sentinel, so the role falls back to the workspace default.
    const body: Pick<RoleAssignment, "modelId"> = { modelId }
    return request<RoleAssignment>(
      `/catalog/assignments/${encodeURIComponent(role)}`,
      {
        method: "PUT",
        body: JSON.stringify(body),
      },
    )
  },

  // --- usage (spec 10.9) ----------------------------------------------------

  getUsage(): Promise<UsageResponse> {
    return request<UsageResponse>("/usage")
  },

  // --- harness run tree (spec v2 §7) ----------------------------------------

  getRunTree(sessionId: string): Promise<AgentRunTreeResponse> {
    return request<AgentRunTreeResponse>(
      `/sessions/${encodeURIComponent(sessionId)}/runs/tree`,
    )
  },

  getRunEvents(
    sessionId: string,
    runId: string,
    opts: { afterSeq?: number } = {},
  ): Promise<AgentEventPage> {
    const query = buildQuery({ afterSeq: opts.afterSeq })
    return request<AgentEventPage>(
      `/sessions/${encodeURIComponent(sessionId)}/runs/${encodeURIComponent(runId)}/events${query}`,
    )
  },
}
