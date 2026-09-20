/**
 * MSW handlers for the Sessions feature (mock layer only).
 *
 * Serves the shapes in `src/api/contract.ts` and simulates loading latency as
 * well as empty and error responses, selected via the `x-mock-scenario` header
 * the API client attaches.
 *
 * A session's event stream carries the status projection and, while the session
 * is running, the harness frames the run store has queued for it, so the run
 * tree animates the way it does against a real worker.
 */

import { HttpResponse, delay, http } from "msw"

import type {
  ApiErrorBody,
  DateRangePreset,
  Paginated,
  RetrySessionRequest,
  ReviewSession,
  SessionFilterOptions,
  SessionStats,
  SessionStatus,
  SessionTarget,
  TargetStatus,
} from "@/api/contract"
import { dataset } from "./dataset"
import {
  LIVE_FRAME_INTERVAL_MS,
  commitLiveFrame,
  liveTargetStatus,
  peekLiveFrame,
} from "./runs"

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "/api"

/** Pause before a running session's first live frame, so a cold tree read lands first. */
const LIVE_WARMUP_MS = 900

/**
 * The session with any target status the live stream has moved past the
 * dataset's. A target whose orchestrator the simulation just started is
 * running, whatever the dataset was seeded with, so the detail view does not
 * read `queued` beside its own running run.
 */
function withLiveTargets(session: ReviewSession): ReviewSession {
  return {
    ...session,
    targets: session.targets.map((target) => ({
      ...target,
      status: liveTargetStatus(session, target.id) ?? target.status,
    })),
  }
}

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

/** The statuses a manual retry may re-queue, and the ones it refuses. */
const RETRYABLE_TARGET_STATUS: Partial<Record<TargetStatus, true>> = {
  failed: true,
  cancelled: true,
}
const ACTIVE_TARGET_STATUS: Partial<Record<TargetStatus, true>> = {
  queued: true,
  running: true,
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

    const encoder = new TextEncoder()
    const encode = (event: string, payload: string): Uint8Array =>
      encoder.encode(`event: ${event}\ndata: ${payload}\n\n`)
    const projection = (): string => {
      const live = withLiveTargets(found)
      return JSON.stringify({
        id: live.id,
        status: live.status,
        targets: live.targets.map((target) => ({
          id: target.id,
          number: target.number,
          status: target.status,
        })),
      })
    }

    // Deliberately pull-driven (mock only): the simulated worker only moves as
    // far as the client actually reads, so a connection dropped mid-stream
    // (a StrictMode remount, a closed tab) leaves the run store where its
    // frames got to, and the next connection picks up the rest.
    let liveFrames = 0
    let gone = false
    const opened = projection()
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(encode("session", opened))
      },
      async pull(controller) {
        if (gone || request.signal.aborted) return
        const frame = peekLiveFrame(found)
        if (!frame) {
          // A session with nothing live is the stream it always was: one
          // snapshot, then the close that repeats it.
          if (liveFrames === 0) {
            controller.enqueue(encode("done", opened))
          } else {
            const payload = projection()
            controller.enqueue(encode("session", payload))
            controller.enqueue(encode("done", payload))
          }
          controller.close()
          return
        }
        // The first live frame waits a beat longer, so the run tree a cold
        // client is still loading lands before the frames start folding onto it.
        await delay(liveFrames === 0 ? LIVE_WARMUP_MS : LIVE_FRAME_INTERVAL_MS)
        try {
          controller.enqueue(encode("agent", JSON.stringify(frame.event)))
        } catch {
          return
        }
        liveFrames += 1
        commitLiveFrame(found, frame)
      },
      cancel() {
        gone = true
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
    return HttpResponse.json(withLiveTargets(found))
  }),

  /**
   * Manual retry (spec 10.5 §Manual retry). The selection rules mirror the
   * server's, so the refusals the detail view has to surface (`target_running`,
   * `nothing_to_retry`) are reachable here too. No worker runs in the mock
   * stack, so a requeued session stays queued until it is submitted again.
   */
  http.post(`${API_BASE}/sessions/:id/retry`, async ({ request, params }) => {
    await latency(request)
    if (isErrorScenario(request)) {
      return errorResponse(
        500,
        "retry_unavailable",
        "Could not retry the session.",
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

    let body: RetrySessionRequest = {}
    try {
      body = (await request.json()) as RetrySessionRequest
    } catch {
      body = {}
    }

    const selection: SessionTarget[] = []
    for (const targetId of new Set(body.targetIds ?? [])) {
      const target = found.targets.find((row) => row.id === targetId)
      if (!target) {
        return errorResponse(
          404,
          "target_not_found",
          "That review target is not part of this session.",
          `No target with id ${targetId}.`,
        )
      }
      selection.push(target)
    }
    if (selection.length === 0) {
      selection.push(
        ...found.targets.filter(
          (target) => RETRYABLE_TARGET_STATUS[target.status],
        ),
      )
    }

    const busy = selection.find((target) => ACTIVE_TARGET_STATUS[target.status])
    if (busy) {
      return errorResponse(
        409,
        "target_running",
        "A review target in this selection is already queued or running.",
        `Target ${busy.id} is ${busy.status}.`,
      )
    }
    const retryable = selection.filter(
      (target) => RETRYABLE_TARGET_STATUS[target.status],
    )
    if (retryable.length === 0) {
      return errorResponse(
        409,
        "nothing_to_retry",
        "This session has no failed or cancelled targets to retry.",
      )
    }

    for (const target of retryable) {
      target.status = "queued"
      // Back on the queue: it is not a retry candidate again until it settles.
      target.retryAction = null
    }
    found.status = "queued"
    // The session is in flight again, so the fields that only describe a
    // finished one go with it.
    found.finishedAt = undefined
    found.durationMs = undefined
    return HttpResponse.json(withLiveTargets(found))
  }),
]

export type { ReviewSession }
