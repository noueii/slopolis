/**
 * MSW handlers for the Sessions feature (mock layer only).
 *
 * Serves the shapes in `src/api/contract.ts` and simulates loading latency as
 * well as empty and error responses, selected via the `x-mock-scenario` header
 * the API client attaches.
 */

import { HttpResponse, delay, http } from "msw"

import type {
  ApiErrorBody,
  DateRangePreset,
  Paginated,
  ReviewSession,
  SessionFilterOptions,
  SessionStats,
  SessionStatus,
} from "@/api/contract"
import { dataset } from "./dataset"

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "/api"

const RANGE_MS: Record<Exclude<DateRangePreset, "all">, number> = {
  "24h": 24 * 60 * 60 * 1000,
  "7d": 7 * 24 * 60 * 60 * 1000,
  "30d": 30 * 24 * 60 * 60 * 1000,
  "90d": 90 * 24 * 60 * 60 * 1000,
}

function scenarioOf(request: Request): string {
  return request.headers.get("x-mock-scenario") ?? "default"
}

function isEmptyScenario(request: Request): boolean {
  return scenarioOf(request) === "empty"
}

function isErrorScenario(request: Request): boolean {
  return scenarioOf(request) === "error"
}

async function latency(request: Request): Promise<void> {
  if (scenarioOf(request) === "slow") {
    await delay(2200)
    return
  }
  await delay(320 + Math.floor(Math.random() * 420))
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

function matchesQuery(session: ReviewSession, q: string): boolean {
  const needle = q.toLowerCase()
  const haystacks: string[] = [
    session.name,
    session.model,
    session.provider,
    session.status,
    session.triggeredBy.handle,
    session.triggeredBy.name,
  ]
  for (const target of session.targets) {
    haystacks.push(
      target.repository.fullName,
      `#${target.number}`,
      `${target.repository.fullName}#${target.number}`,
      target.title,
    )
  }
  return haystacks.some((value) => value.toLowerCase().includes(needle))
}

function emptyPage(page: number, pageSize: number): Paginated<ReviewSession> {
  return { items: [], page, pageSize, total: 0, totalPages: 0 }
}

const EMPTY_FILTER_OPTIONS: SessionFilterOptions = {
  repositories: [],
  users: [],
  statuses: [],
  models: [],
}

const EMPTY_STATS: SessionStats = {
  totalSessions: 0,
  running: 0,
  failed: 0,
  tokens: 0,
  costUsd: 0,
}

export const sessionsHandlers = [
  http.get(`${API_BASE}/sessions/filters`, async ({ request }) => {
    await latency(request)
    if (isErrorScenario(request)) {
      return errorResponse(
        500,
        "filters_unavailable",
        "Could not load filter options.",
        "The workspace repository index is temporarily unavailable.",
      )
    }
    return HttpResponse.json(
      isEmptyScenario(request) ? EMPTY_FILTER_OPTIONS : dataset.filterOptions,
    )
  }),

  http.get(`${API_BASE}/sessions/stats`, async ({ request }) => {
    await latency(request)
    if (isErrorScenario(request)) {
      return errorResponse(500, "stats_unavailable", "Could not load usage stats.")
    }
    return HttpResponse.json(
      isEmptyScenario(request) ? EMPTY_STATS : dataset.stats,
    )
  }),

  http.get(`${API_BASE}/sessions`, async ({ request }) => {
    await latency(request)
    if (isErrorScenario(request)) {
      return errorResponse(
        500,
        "sessions_unavailable",
        "Could not load review sessions.",
        "Upstream request to the sessions service timed out after 30s.",
      )
    }

    const url = new URL(request.url)
    const params = url.searchParams
    const page = Math.max(1, Number(params.get("page") ?? "1") || 1)
    const pageSize = Math.min(
      100,
      Math.max(1, Number(params.get("pageSize") ?? "25") || 25),
    )
    const q = params.get("q")?.trim() ?? ""
    const repo = params.get("repo") ?? ""
    const user = params.get("user") ?? ""
    const status = (params.get("status") ?? "") as SessionStatus | ""
    const range = (params.get("range") ?? "") as Exclude<DateRangePreset, "all"> | ""
    const sort = params.get("sort") ?? "created_desc"

    if (isEmptyScenario(request)) {
      return HttpResponse.json(emptyPage(page, pageSize))
    }

    const now = Date.now()
    let rows = dataset.sessions.filter((session) => {
      if (q && !matchesQuery(session, q)) return false
      if (repo && !session.targets.some((t) => t.repository.fullName === repo))
        return false
      if (user && session.triggeredBy.handle !== user) return false
      if (status && session.status !== status) return false
      if (range && RANGE_MS[range]) {
        const age = now - new Date(session.createdAt).getTime()
        if (age > RANGE_MS[range]) return false
      }
      return true
    })

    rows = rows.sort((a, b) => {
      switch (sort) {
        case "created_asc":
          return new Date(a.createdAt).getTime() - new Date(b.createdAt).getTime()
        case "cost_desc":
          return b.costUsd - a.costUsd
        case "tokens_desc":
          return b.tokens - a.tokens
        case "created_desc":
        default:
          return new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime()
      }
    })

    const total = rows.length
    const totalPages = total === 0 ? 0 : Math.ceil(total / pageSize)
    const start = (page - 1) * pageSize
    const items = rows.slice(start, start + pageSize)

    const payload: Paginated<ReviewSession> = {
      items,
      page,
      pageSize,
      total,
      totalPages,
    }
    return HttpResponse.json(payload)
  }),

  http.get(`${API_BASE}/sessions/:id/events`, async ({ request, params }) => {
    await latency(request)
    const found = dataset.sessions.find((s) => s.id === params.id)
    if (!found) {
      return errorResponse(
        404,
        "session_not_found",
        "That review session does not exist.",
        `No session with id "${String(params.id)}".`,
      )
    }

    const payload = JSON.stringify({
      id: found.id,
      status: found.status,
      targets: found.targets.map((target) => ({
        id: target.id,
        number: target.number,
        status: target.status,
      })),
    })
    const frames = [
      `event: session\ndata: ${payload}\n\n`,
      `event: done\ndata: ${payload}\n\n`,
    ]
    const encoder = new TextEncoder()
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        for (const frame of frames) controller.enqueue(encoder.encode(frame))
        controller.close()
      },
    })
    return new HttpResponse(stream, {
      headers: {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
      },
    })
  }),

  http.get(`${API_BASE}/sessions/:id`, async ({ request, params }) => {
    await latency(request)
    if (isErrorScenario(request)) {
      return errorResponse(
        500,
        "session_unavailable",
        "Could not load the session.",
      )
    }
    const found = dataset.sessions.find((s) => s.id === params.id)
    if (!found) {
      return errorResponse(
        404,
        "session_not_found",
        "That review session does not exist.",
        `No session with id "${String(params.id)}".`,
      )
    }
    return HttpResponse.json(found)
  }),
]

export type { ReviewSession }
