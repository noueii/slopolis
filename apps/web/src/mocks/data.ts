/**
 * Deterministic mock dataset.
 *
 * Everything here is generated from a fixed seed so the mock is stable across
 * reloads (important for design review and screenshots). This module is only
 * imported by the MSW layer.
 */

import type {
  RepositoryRef,
  ReviewSession,
  Severity,
  SessionFilterOptions,
  SessionStatus,
  SessionStats,
  SessionTarget,
  TargetStatus,
  UserRef,
} from "@/api/contract"

export const REPOS: Array<{ fullName: string; isPrivate: boolean }> = [
  { fullName: "acme/api-gateway", isPrivate: true },
  { fullName: "acme/web-console", isPrivate: true },
  { fullName: "acme/billing-service", isPrivate: true },
  { fullName: "acme/auth-service", isPrivate: false },
  { fullName: "acme/infra-terraform", isPrivate: true },
  { fullName: "acme/design-system", isPrivate: false },
  { fullName: "orbit-labs/telemetry-sdk", isPrivate: false },
  { fullName: "orbit-labs/edge-router", isPrivate: true },
  { fullName: "noueii/slopolis", isPrivate: false },
  { fullName: "noueii/dotfiles", isPrivate: false },
]

export const USERS: Array<{ handle: string; name: string }> = [
  { handle: "noueii", name: "Noah Yu" },
  { handle: "mira.k", name: "Mira Kim" },
  { handle: "dev-arya", name: "Arya Prasad" },
  { handle: "jpark", name: "Jiho Park" },
  { handle: "lena", name: "Lena Brandt" },
  { handle: "t.okada", name: "Taro Okada" },
  { handle: "sam.w", name: "Sam Whitfield" },
  { handle: "rafael", name: "Rafael Costa" },
]

const MODELS: Array<{ id: string; provider: string; pricePer1k: number }> = [
  { id: "gpt-4o", provider: "OpenAI", pricePer1k: 0.006 },
  { id: "gpt-4o-mini", provider: "OpenAI", pricePer1k: 0.0006 },
  { id: "claude-sonnet-4", provider: "Anthropic", pricePer1k: 0.0045 },
  { id: "claude-opus-4", provider: "Anthropic", pricePer1k: 0.021 },
  { id: "gemini-2.5-pro", provider: "Google", pricePer1k: 0.0038 },
  { id: "llama-3.3-70b", provider: "Groq", pricePer1k: 0.0009 },
]

const TARGET_STATUS_WEIGHTS: Array<[TargetStatus, number]> = [
  ["done", 0.62],
  ["running", 0.12],
  ["failed", 0.12],
  ["cancelled", 0.08],
  ["queued", 0.06],
]

const SESSION_STATUS_WEIGHTS: Array<[SessionStatus, number]> = [
  ["done", 0.6],
  ["failed", 0.14],
  ["running", 0.12],
  ["cancelled", 0.08],
  ["queued", 0.06],
]

const PROMPTS = [
  "Flag N+1 queries and any missing authorization checks. Keep nits out.",
  "Focus on race conditions in the worker pool and how errors propagate.",
  "Check the migration for backward compatibility and missing indexes.",
  "Prioritize security: input validation, auth boundaries, and secret handling.",
  "Review API changes for breaking changes and missing test coverage.",
  "Look for unbounded memory growth in long-lived processes.",
  "Verify retry, timeout, and idempotency behavior around network calls.",
  "Call out any place where errors are swallowed instead of surfaced.",
]

export const PR_TITLES = [
  "fix: guard token refresh against clock skew",
  "feat: add per-repo concurrency limits",
  "refactor: split session repository into read/write paths",
  "perf: batch usage records before insert",
  "fix: dedupe PR URLs during pre-flight",
  "feat: stream target progress over SSE",
  "chore: bump githubkit and regenerate types",
  "fix: retry target on transient 502 from provider",
  "feat: expose cost breakdown by model",
  "test: cover cancellation race in worker pool",
  "fix: render inline suggestions on unchanged lines",
  "feat: workspace model assignment defaults to auto",
]

function mulberry32(seed: number) {
  let state = seed
  return () => {
    state |= 0
    state = (state + 0x6d2b79f5) | 0
    let t = Math.imul(state ^ (state >>> 15), 1 | state)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

function pickWeighted<T>(rand: () => number, weights: Array<[T, number]>): T {
  const roll = rand()
  let cumulative = 0
  for (const [value, weight] of weights) {
    cumulative += weight
    if (roll <= cumulative) return value
  }
  return weights[weights.length - 1][0]
}

function pad(value: number): string {
  return value.toString().padStart(2, "0")
}

function formatShortDate(date: Date): string {
  const months = [
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
  ]
  return `${months[date.getMonth()]} ${date.getDate()}`
}

function headBranchFromTitle(title: string): string {
  const match = title.match(/^([a-z]+)(?:\([^)]*\))?:\s*(.+)$/i)
  const type = (match?.[1] ?? "feat").toLowerCase()
  const subject = match?.[2] ?? title
  const slug = subject
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
  return slug ? `${type}/${slug}` : type
}

const HOUR = 60 * 60 * 1000
const DAY = 24 * HOUR

export interface MockDataset {
  sessions: ReviewSession[]
  generatedAt: number
  filterOptions: SessionFilterOptions
  stats: SessionStats
}

export function createDataset(now = Date.now()): MockDataset {
  const rand = mulberry32(0x510f0115)
  const sessions: ReviewSession[] = []

  for (let i = 0; i < 57; i += 1) {
    const user = USERS[Math.floor(rand() * USERS.length)]
    const model = MODELS[Math.floor(rand() * MODELS.length)]
    const status = pickWeighted(rand, SESSION_STATUS_WEIGHTS)

    // Skew creation times toward the recent past (0-80 days).
    const daysAgo = Math.floor(rand() ** 2 * 80)
    const minutesAgo = Math.floor(rand() * 24 * 60)
    const createdMs = now - daysAgo * DAY - minutesAgo * 60 * 1000
    const created = new Date(createdMs)

    const targetCount = 1 + Math.floor(rand() * 4)
    const repos = [...REPOS].sort(() => rand() - 0.5).slice(0, targetCount)

    const targets: SessionTarget[] = repos.map((repo, index) => {
      const number = 40 + Math.floor(rand() * 900)
      const title = PR_TITLES[Math.floor(rand() * PR_TITLES.length)]
      const targetStatus: TargetStatus =
        status === "done"
          ? "done"
          : status === "queued"
            ? "queued"
            : status === "cancelled"
              ? pickWeighted(rand, [
                  ["cancelled", 0.6],
                  ["done", 0.4],
                ])
              : status === "failed"
                ? pickWeighted(rand, [
                    ["failed", 0.45],
                    ["done", 0.45],
                    ["running", 0.1],
                  ])
                : pickWeighted(rand, TARGET_STATUS_WEIGHTS)

      const baseTokens =
        targetStatus === "queued"
          ? 0
          : 9000 + Math.floor(rand() * 68000)
      const doneRatio =
        targetStatus === "done"
          ? 1
          : targetStatus === "running"
            ? 0.35 + rand() * 0.4
            : targetStatus === "failed"
              ? 0.4 + rand() * 0.5
              : 0
      const tokens = Math.round(baseTokens * doneRatio)
      const costUsd = Number(((tokens / 1000) * model.pricePer1k).toFixed(4))
      const findingsCount =
        targetStatus === "done" ? Math.floor(rand() * 14) : Math.floor(rand() * 4)

      return {
        id: `tgt_${i}_${index}`,
        repository: {
          id: `repo_${repo.fullName.replace(/[^a-z0-9]/gi, "_")}`,
          fullName: repo.fullName,
          private: repo.isPrivate,
          defaultBranch: rand() > 0.8 ? "develop" : "main",
        } satisfies RepositoryRef,
        number,
        title,
        url: `https://github.com/${repo.fullName}/pull/${number}`,
        headBranch: headBranchFromTitle(title),
        status: targetStatus,
        findingsCount,
        tokens,
        costUsd,
      }
    })

    const tokens = targets.reduce((sum, t) => sum + t.tokens, 0)
    const costUsd = Number(
      targets.reduce((sum, t) => sum + t.costUsd, 0).toFixed(4),
    )
    const findingsCount = targets.reduce((sum, t) => sum + t.findingsCount, 0)

    const first = targets[0]
    const leadName = `${first.repository.fullName}#${first.number}`
    const extra = targets.length > 1 ? ` +${targets.length - 1} more` : ""
    const name = `${leadName}${extra} — ${formatShortDate(created)}`

    const startedAt =
      status === "queued"
        ? undefined
        : new Date(createdMs + (2 + Math.floor(rand() * 20)) * 1000).toISOString()
    const activeMs = 45_000 + Math.floor(rand() * 26 * 60_000)
    const finishedAt =
      status === "done" || status === "failed" || status === "cancelled"
        ? new Date(createdMs + activeMs).toISOString()
        : undefined
    const durationMs =
      status === "running" ? now - createdMs : finishedAt ? activeMs : undefined

    sessions.push({
      id: `ses_${(i + 1).toString().padStart(4, "0")}${Math.floor(rand() * 46656)
        .toString(36)
        .padStart(3, "0")}`,
      name,
      status,
      model: model.id,
      provider: model.provider,
      triggeredBy: {
        id: `usr_${user.handle}`,
        handle: user.handle,
        name: user.name,
        avatarUrl: undefined,
      },
      createdAt: created.toISOString(),
      startedAt,
      finishedAt,
      durationMs,
      targets,
      targetCount: targets.length,
      tokens,
      costUsd,
      findingsCount,
      prompt:
        rand() > 0.4
          ? PROMPTS[Math.floor(rand() * PROMPTS.length)]
          : undefined,
    })
  }

  sessions.sort(
    (a, b) => new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime(),
  )

  return {
    sessions,
    generatedAt: now,
    filterOptions: buildFilterOptions(sessions),
    stats: buildStats(sessions),
  }
}

function buildFilterOptions(sessions: ReviewSession[]): SessionFilterOptions {
  const repos = new Map<string, number>()
  const users = new Map<string, { name: string; count: number }>()
  for (const session of sessions) {
    for (const target of session.targets) {
      repos.set(target.repository.fullName, (repos.get(target.repository.fullName) ?? 0) + 1)
    }
    const key = session.triggeredBy.handle
    const existing = users.get(key)
    users.set(key, {
      name: session.triggeredBy.name,
      count: (existing?.count ?? 0) + 1,
    })
  }

  return {
    repositories: [...repos.entries()]
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([fullName, count]) => ({
        value: fullName,
        label: fullName,
        hint: `${count}`,
      })),
    users: [...users.entries()]
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([handle, info]) => ({
        value: handle,
        label: `@${handle}`,
        hint: info.name,
      })),
    statuses: (
      ["queued", "running", "done", "failed", "cancelled"] as SessionStatus[]
    ).map((status) => ({
      value: status,
      label: STATUS_LABELS[status],
    })),
    models: MODELS.map((model) => ({
      value: model.id,
      label: model.id,
      hint: model.provider,
    })),
  }
}

function buildStats(sessions: ReviewSession[]): SessionStats {
  return {
    totalSessions: sessions.length,
    running: sessions.filter((s) => s.status === "running").length,
    failed: sessions.filter((s) => s.status === "failed").length,
    tokens: sessions.reduce((sum, s) => sum + s.tokens, 0),
    costUsd: Number(
      sessions.reduce((sum, s) => sum + s.costUsd, 0).toFixed(2),
    ),
  }
}

export const STATUS_LABELS: Record<SessionStatus, string> = {
  queued: "Queued",
  running: "Running",
  done: "Done",
  failed: "Failed",
  cancelled: "Cancelled",
}

export const SEVERITY_ORDER: Severity[] = ["info", "warning", "error", "critical"]
