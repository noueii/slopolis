/**
 * MSW handler for the Usage report (mock layer only). Every number is derived
 * from the shared session dataset, so the Usage page and the Sessions list
 * describe the same spend; there is no second hard-coded dataset to drift.
 * empty/error/slow are driven by the `x-mock-scenario` header.
 */

import { HttpResponse, delay, http } from "msw"

import type {
  ApiErrorBody,
  ReviewSession,
  UsageBreakdown,
  UsagePoint,
  UsageResponse,
} from "@/api/contract"
import { dataset } from "./dataset"

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "/api"

/** What a workspace with nothing recorded reports. */
export const EMPTY_USAGE: UsageResponse = {
  totalTokens: 0,
  totalCostUsd: 0,
  totalSessions: 0,
  byModel: [],
  byRepository: [],
  byUser: [],
  series: [],
}

interface Bucket {
  label: string
  tokens: number
  costUsd: number
  sessionIds: Set<string>
}

function scenarioOf(request: Request): string {
  return request.headers.get("x-mock-scenario") ?? "default"
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

/**
 * The server sorts every breakdown by tokens descending; the key breaks ties
 * so the order is total instead of depending on dict insertion order.
 */
function byTokens(a: UsageBreakdown, b: UsageBreakdown): number {
  return b.tokens - a.tokens || a.key.localeCompare(b.key)
}

function contribute(
  buckets: Map<string, Bucket>,
  key: string,
  label: string,
  tokens: number,
  costUsd: number,
  sessionId: string,
): void {
  // A bucket only lists sessions that put at least one token into it, so a
  // queued target does not inflate its repository's session count.
  if (tokens <= 0) return
  const bucket = buckets.get(key) ?? {
    label,
    tokens: 0,
    costUsd: 0,
    sessionIds: new Set<string>(),
  }
  bucket.tokens += tokens
  bucket.costUsd += costUsd
  bucket.sessionIds.add(sessionId)
  buckets.set(key, bucket)
}

function toBreakdowns(buckets: Map<string, Bucket>): UsageBreakdown[] {
  return [...buckets.entries()]
    .map(([key, bucket]) => ({
      key,
      label: bucket.label,
      tokens: bucket.tokens,
      costUsd: Number(bucket.costUsd.toFixed(6)),
      sessions: bucket.sessionIds.size,
    }))
    .sort(byTokens)
}

export function buildUsage(sessions: readonly ReviewSession[]): UsageResponse {
  const models = new Map<string, Bucket>()
  const repositories = new Map<string, Bucket>()
  const users = new Map<string, Bucket>()
  const days = new Map<string, UsagePoint>()

  let totalTokens = 0
  let totalCostUsd = 0
  let totalSessions = 0

  for (const session of sessions) {
    totalTokens += session.tokens
    totalCostUsd += session.costUsd

    // Usage is recorded per target, so a session with no recorded tokens has no
    // record — and therefore no day or session — to account for, just as the
    // server's totals only count sessions that produced records.
    if (session.tokens > 0) {
      totalSessions += 1
      const day = session.createdAt.slice(0, 10)
      const point =
        days.get(day) ?? { date: day, tokens: 0, costUsd: 0, sessions: 0 }
      point.tokens += session.tokens
      point.costUsd += session.costUsd
      point.sessions += 1
      days.set(day, point)
    }

    // Keys mirror the server: the model id, the repository full name, and the
    // triggering user's id, labelled with the provider, the full name, and the
    // user's GitHub handle respectively.
    contribute(
      models,
      session.model,
      session.provider,
      session.tokens,
      session.costUsd,
      session.id,
    )
    contribute(
      users,
      session.triggeredBy.id,
      session.triggeredBy.handle,
      session.tokens,
      session.costUsd,
      session.id,
    )
    for (const target of session.targets) {
      contribute(
        repositories,
        target.repository.fullName,
        target.repository.fullName,
        target.tokens,
        target.costUsd,
        session.id,
      )
    }
  }

  const series = [...days.values()]
    .sort((a, b) => a.date.localeCompare(b.date))
    .map((point) => ({ ...point, costUsd: Number(point.costUsd.toFixed(6)) }))

  return {
    totalTokens,
    totalCostUsd: Number(totalCostUsd.toFixed(6)),
    totalSessions,
    byModel: toBreakdowns(models),
    byRepository: toBreakdowns(repositories),
    byUser: toBreakdowns(users),
    series,
  }
}

export const usageHandlers = [
  http.get(`${API_BASE}/usage`, async ({ request }) => {
    await latency(request)
    if (scenarioOf(request) === "error") {
      return errorResponse(
        500,
        "usage_unavailable",
        "Could not load the usage report.",
        "The usage roll-up query timed out after 30s.",
      )
    }

    return HttpResponse.json(
      scenarioOf(request) === "empty" ? EMPTY_USAGE : buildUsage(dataset.sessions),
    )
  }),
]
