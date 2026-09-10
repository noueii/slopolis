/**
 * slopolis API client.
 *
 * UI code talks to the backend exclusively through this module. The fetch
 * layer is mock-agnostic: when MSW is active it intercepts these requests at
 * the network boundary, and when the real FastAPI server exists the same
 * calls hit it unchanged.
 */

import type {
  ApiErrorBody,
  CreateReviewRequest,
  CreatedSession,
  DashboardData,
  DashboardParams,
  ModelCatalog,
  Paginated,
  PreflightRequest,
  PreflightResult,
  RepositoryListResponse,
  RepositoryPullRequestsResponse,
  ReviewSession,
  SessionFilterOptions,
  SessionListParams,
  SessionStats,
} from "./contract"

const API_BASE = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "/api"

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
  const flag = import.meta.env.VITE_MOCK
  if (flag === "0") return false
  if (flag === "1") return true
  return import.meta.env.DEV
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
  // Ignored by a real backend; read by the MSW handlers.
  headers.set("x-mock-scenario", mockScenario)

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
}
