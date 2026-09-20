/**
 * MSW handlers for the Dashboard (home) plus the New Review composer's
 * pre-flight and create calls. Serves the shapes in `src/api/contract.ts`;
 * empty/error/slow are driven by the `x-mock-scenario` header.
 */

import { HttpResponse, delay, http } from "msw"

import type {
  ApiErrorBody,
  CreatedSession,
  DashboardData,
  DashboardSession,
  DashboardSummary,
  LiveSession,
  ModelCatalog,
  OpenPullRequest,
  PrReference,
  PreflightResult,
  PullRequestChecks,
  RepositoryRef,
  RepositoryPullRequestsResponse,
  RepositorySummary,
  RepositoryUpdate,
  RequiredAccess,
  ReviewPresetCatalog,
  ReviewSession,
} from "@/api/contract"
import { PR_TITLES, REPOS, USERS, reviewTitleFromTitle } from "./data"
import { MODEL_CATALOG, REVIEW_PRESET_CATALOG, dataset } from "./dataset"

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "/api"

const PR_URL_PATTERN =
  /^https?:\/\/github\.com\/([^/\s]+)\/([^/\s]+)\/pull\/(\d+)/i

const STEPS = [
  "Fetching pull request metadata",
  "Snapshotting repository at head",
  "Chunking diff and surrounding context",
  "Scanning changed files for defects",
  "Cross-checking repository conventions",
  "Ranking and drafting findings",
  "Publishing review comments",
]

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

function hash(input: string): number {
  let value = 0
  for (let i = 0; i < input.length; i += 1) {
    value = (value * 31 + input.charCodeAt(i)) | 0
  }
  return Math.abs(value)
}

function repoId(fullName: string): string {
  return `repo_${fullName.replace(/[^a-z0-9]/gi, "_")}`
}

/**
 * Repositories the workspace has switched off (spec 10.1). They stay listed and
 * keep their history; pre-flight refuses their pull requests. Seeded so the
 * parked state is visible without clicking anything, and mutable so the mock's
 * PATCH behaves like the real switch.
 */
const parkedRepositories = new Set(
  REPOS.filter((_repo, index) => index % 5 === 2).map((repo) => repo.fullName),
)

/** The only values the API accepts for `requiredAccess` (spec 10.10). */
const REQUIRED_ACCESS_VALUES: readonly RequiredAccess[] = [
  "default",
  "read",
  "write",
]

/**
 * Per-repository triggering rules (spec 10.10), keyed by full name. Seeded so
 * the override is visible without clicking anything: every seventh repository
 * loosens to read, every third tightens to write, the rest keep the spec rule.
 * Mutable so the mock's PATCH behaves like the real switch.
 */
const requiredAccessByRepo: Record<string, RequiredAccess> = Object.fromEntries(
  REPOS.map((repo, index) => [
    repo.fullName,
    index % 7 === 3 ? "read" : index % 3 === 1 ? "write" : "default",
  ]),
)

function buildRepositories(now = Date.now()): RepositorySummary[] {
  return REPOS.map((repo, index) => ({
    id: repoId(repo.fullName),
    fullName: repo.fullName,
    private: repo.isPrivate,
    defaultBranch: index % 5 === 0 ? "develop" : "main",
    openPrCount: OPEN_PULL_REQUESTS.get(repo.fullName)?.length ?? 0,
    lastActivityAt: new Date(now - (index + 1) * (37 * 60 * 1000)).toISOString(),
    // Seeded policies keep the override visible in the mock app: every third
    // repository tightens to write, every seventh loosens to read.
    requiredAccess: requiredAccessByRepo[repo.fullName] ?? "default",
    connected: true,
    enabled: !parkedRepositories.has(repo.fullName),
  }))
}

function buildOpenPullRequests(
  now = Date.now(),
): Map<string, OpenPullRequest[]> {
  const byRepo = new Map<string, OpenPullRequest[]>()

  for (const repo of REPOS) {
    const count = 3 + (hash(`${repo.fullName}:count`) % 7)
    const pulls: OpenPullRequest[] = []

    for (let i = 0; i < count; i += 1) {
      const pick = (salt: string, mod: number) =>
        hash(`${repo.fullName}#${i}:${salt}`) % mod
      const author = USERS[pick("author", USERS.length)]
      const total = 2 + pick("total", 5)
      const roll = pick("roll", 100)
      const passing =
        roll < 62 ? total : roll < 82 ? Math.max(1, Math.floor(total / 2)) : 0
      const state: PullRequestChecks["state"] =
        passing === 0 ? "failing" : passing >= total ? "passing" : "pending"
      const number = 40 + pick("number", 900)

      pulls.push({
        id: `pr_${repo.fullName.replace(/[^a-z0-9]/gi, "_")}_${i}`,
        repository: repositoryRef(repo.fullName, repo.isPrivate),
        number,
        title: PR_TITLES[pick("title", PR_TITLES.length)],
        url: `https://github.com/${repo.fullName}/pull/${number}`,
        author: {
          id: `usr_${author.handle}`,
          handle: author.handle,
          name: author.name,
          isAdmin: author.handle === "noueii",
        },
        updatedAt: new Date(
          now - (12 + pick("updated", 72 * 60)) * 60 * 1000,
        ).toISOString(),
        draft: pick("draft", 7) === 0,
        comments: pick("comments", 24),
        changedFiles: 1 + pick("files", 42),
        additions: 4 + pick("additions", 480),
        deletions: pick("deletions", 220),
        checks: { state, total, passing },
      })
    }

    pulls.sort(
      (a, b) =>
        new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime(),
    )
    byRepo.set(repo.fullName, pulls)
  }

  return byRepo
}

const OPEN_PULL_REQUESTS = buildOpenPullRequests()

const REPOSITORY_INDEX = new Map(
  REPOS.map((repo) => [repo.fullName.toLowerCase(), repo]),
)

function repositoryRef(fullName: string, isPrivate: boolean): RepositoryRef {
  return {
    id: repoId(fullName),
    fullName,
    private: isPrivate,
    defaultBranch: "main",
  }
}

function toLiveSession(session: ReviewSession, repo: string | null): LiveSession {
  const target =
    (repo
      ? session.targets.find(
          (item) => item.repository.fullName === repo,
        )
      : undefined) ??
    session.targets.find((item) => item.status === "running") ??
    session.targets[0]
  const seed = hash(session.id)
  const progress = 16 + (seed % 66)
  const stepIndex = Math.min(
    STEPS.length - 1,
    Math.floor((progress / 100) * STEPS.length),
  )
  const elapsedMs = (3 + (seed % 38)) * 60 * 1000
  const startedAt = new Date(Date.now() - elapsedMs).toISOString()

  return {
    id: session.id,
    name: session.name,
    status: "running",
    repository: target.repository,
    number: target.number,
    prLabel: `${target.repository.fullName}#${target.number}`,
    title: session.title,
    url: target.url,
    headBranch: target.headBranch,
    model: session.model,
    provider: session.provider,
    progress,
    step: STEPS[stepIndex],
    startedAt,
    elapsedMs,
  }
}

function toDashboardSession(session: ReviewSession): DashboardSession {
  return {
    id: session.id,
    title: session.title,
    name: session.name,
    status: session.status,
    model: session.model,
    provider: session.provider,
    prompt: session.prompt,
    createdAt: session.createdAt,
    finishedAt: session.finishedAt,
    targets: session.targets,
    targetCount: session.targetCount,
    findingsCount: session.findingsCount,
    costUsd: session.costUsd,
  }
}

function scopedSessions(repo: string | null): ReviewSession[] {
  if (!repo) return dataset.sessions
  return dataset.sessions.filter((session) =>
    session.targets.some((target) => target.repository.fullName === repo),
  )
}

function buildDashboard(repo: string | null, limit: number): DashboardData {
  const scoped = scopedSessions(repo)
  const running = scoped
    .filter((session) => session.status === "running")
    .map((session) => toLiveSession(session, repo))
  const summary: DashboardSummary = {
    scope: repo ?? "All repositories",
    totalSessions: scoped.length,
    running: running.length,
    failed: scoped.filter((session) => session.status === "failed").length,
    spendUsd: Number(
      scoped.reduce((sum, session) => sum + session.costUsd, 0).toFixed(2),
    ),
    tokens: scoped.reduce((sum, session) => sum + session.tokens, 0),
  }

  return {
    scope: summary.scope,
    summary,
    running,
    recent: scoped.slice(0, limit).map(toDashboardSession),
    generatedAt: new Date().toISOString(),
  }
}

function normalizePrUrl(raw: string): {
  fullName: string
  number: number
  url: string
} | null {
  const match = raw.trim().match(PR_URL_PATTERN)
  if (!match) return null
  const fullName = `${match[1]}/${match[2]}`
  const number = Number(match[3])
  return {
    fullName,
    number,
    url: `https://github.com/${fullName}/pull/${number}`,
  }
}

function resolvePreflight(prUrls: string[]): PreflightResult {
  const valid: PrReference[] = []
  const invalid: string[] = []
  const notices: string[] = []
  const seen = new Set<string>()
  let duplicates = 0

  for (const raw of prUrls) {
    const trimmed = raw.trim()
    if (!trimmed) continue

    const parsed = normalizePrUrl(trimmed)
    if (!parsed) {
      invalid.push(trimmed)
      continue
    }
    if (seen.has(parsed.url)) {
      duplicates += 1
      continue
    }
    seen.add(parsed.url)

    const repo = REPOSITORY_INDEX.get(parsed.fullName.toLowerCase())
    if (!repo) {
      invalid.push(parsed.url)
      notices.push(`${parsed.fullName} is not covered by this workspace.`)
      continue
    }

    if (parkedRepositories.has(repo.fullName)) {
      invalid.push(parsed.url)
      notices.push(
        `Repository ${repo.fullName} is disabled in slopolis, so its pull requests are not reviewed. Enable it under Repositories to review them again.`,
      )
      continue
    }

    const generated = OPEN_PULL_REQUESTS.get(repo.fullName)?.find(
      (pull) => pull.number === parsed.number,
    )

    valid.push({
      url: parsed.url,
      repository:
        generated?.repository ??
        repositoryRef(repo.fullName, repo.isPrivate),
      number: parsed.number,
      title:
        generated?.title ?? PR_TITLES[hash(parsed.url) % PR_TITLES.length],
    })
  }

  return { valid, invalid, notices: duplicates > 0 ? [...notices, `${duplicates} duplicate link${duplicates === 1 ? "" : "s"} removed.`] : notices }
}

function buildSessionName(prUrls: string[], now = new Date()): string {
  const first = normalizePrUrl(prUrls[0])
  const lead = first ? `${first.fullName}#${first.number}` : "New review"
  const extra = prUrls.length > 1 ? ` +${prUrls.length - 1} more` : ""
  return `${lead}${extra} — ${MONTHS[now.getMonth()]} ${now.getDate()}`
}

const MONTHS = [
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

export const dashboardHandlers = [
  http.get(`${API_BASE}/models`, async ({ request }) => {
    await latency(request)
    if (scenarioOf(request) === "error") {
      return errorResponse(
        500,
        "models_unavailable",
        "Could not load the workspace model catalog.",
        "The provider registry did not respond.",
      )
    }

    const body: ModelCatalog =
      scenarioOf(request) === "empty"
        ? {
            defaultModelId: MODEL_CATALOG.defaultModelId,
            defaultProvider: MODEL_CATALOG.defaultProvider,
            models: [],
          }
        : MODEL_CATALOG
    return HttpResponse.json(body)
  }),

  http.get(`${API_BASE}/presets`, async ({ request }) => {
    await latency(request)
    if (scenarioOf(request) === "error") {
      return errorResponse(
        500,
        "presets_unavailable",
        "Could not load the review presets.",
      )
    }

    const body: ReviewPresetCatalog =
      scenarioOf(request) === "empty"
        ? {
            defaultPresetId: REVIEW_PRESET_CATALOG.defaultPresetId,
            presets: [],
          }
        : {
            defaultPresetId: REVIEW_PRESET_CATALOG.defaultPresetId,
            presets: [...REVIEW_PRESET_CATALOG.presets],
          }
    return HttpResponse.json(body)
  }),

  http.get(`${API_BASE}/repositories`, async ({ request }) => {
    await latency(request)
    if (scenarioOf(request) === "error") {
      return errorResponse(
        500,
        "repositories_unavailable",
        "Could not load connected repositories.",
        "The GitHub App installation listing timed out.",
      )
    }
    const items = scenarioOf(request) === "empty" ? [] : buildRepositories()
    return HttpResponse.json({ items })
  }),

  http.patch(`${API_BASE}/repositories/:id`, async ({ request, params }) => {
    await latency(request)
    if (scenarioOf(request) === "error") {
      return errorResponse(
        500,
        "repository_update_failed",
        "Could not update the repository.",
      )
    }
    const body = (await request.json()) as RepositoryUpdate
    if (body.enabled === undefined && body.requiredAccess === undefined) {
      return errorResponse(
        422,
        "repository_update_empty",
        "Provide the parked state to set the repository to, its review access rule, or both.",
      )
    }
    if (body.enabled !== undefined && typeof body.enabled !== "boolean") {
      return errorResponse(
        422,
        "enabled_required",
        "Provide the parked state to set the repository to as a boolean.",
      )
    }
    if (
      body.requiredAccess !== undefined &&
      !REQUIRED_ACCESS_VALUES.includes(body.requiredAccess)
    ) {
      return errorResponse(
        422,
        "required_access_invalid",
        "Review access must be default, read, or write.",
      )
    }
    const repository = buildRepositories().find(
      (item) => item.id === String(params.id),
    )
    if (!repository) {
      return errorResponse(
        404,
        "repository_not_found",
        "The repository is not connected to this workspace.",
      )
    }
    if (body.enabled !== undefined) {
      if (body.enabled) {
        parkedRepositories.delete(repository.fullName)
      } else {
        parkedRepositories.add(repository.fullName)
      }
    }
    if (body.requiredAccess !== undefined) {
      requiredAccessByRepo[repository.fullName] = body.requiredAccess
    }
    // Both switches are read back out of the stores, so the response is what a
    // later GET serves rather than what this request happened to send.
    const updated =
      buildRepositories().find((item) => item.id === repository.id) ??
      repository
    return HttpResponse.json(updated)
  }),

  http.get(
    `${API_BASE}/repositories/:owner/:name/pulls`,
    async ({ request, params }) => {
      await latency(request)
      if (scenarioOf(request) === "error") {
        return errorResponse(
          500,
          "pull_requests_unavailable",
          "Could not load open pull requests.",
          "The GitHub App installation listing timed out.",
        )
      }

      const fullName = `${params.owner}/${params.name}`
      const repo = REPOSITORY_INDEX.get(fullName.toLowerCase())
      if (!repo) {
        return errorResponse(
          404,
          "repository_not_found",
          `${fullName} is not connected to this workspace.`,
        )
      }

      const repository = buildRepositories().find(
        (item) => item.fullName === repo.fullName,
      )
      if (!repository) {
        return errorResponse(
          500,
          "repository_unavailable",
          "Could not resolve the repository details.",
        )
      }

      const pullRequests =
        scenarioOf(request) === "empty"
          ? []
          : (OPEN_PULL_REQUESTS.get(repo.fullName) ?? [])

      const body: RepositoryPullRequestsResponse = {
        repository,
        pullRequests,
      }
      return HttpResponse.json(body)
    },
  ),

  http.get(`${API_BASE}/dashboard`, async ({ request }) => {
    await latency(request)
    if (scenarioOf(request) === "error") {
      return errorResponse(
        500,
        "dashboard_unavailable",
        "We couldn't reach the sessions service. Try again in a moment.",
        "The dashboard request timed out after 30s.",
      )
    }

    const url = new URL(request.url)
    const repo = url.searchParams.get("repo")?.trim() || null
    const limitRaw = Number(url.searchParams.get("limit") ?? "12")
    const limit = Number.isFinite(limitRaw)
      ? Math.min(50, Math.max(1, limitRaw))
      : 12

    if (scenarioOf(request) === "empty") {
      const empty: DashboardData = {
        scope: repo ?? "All repositories",
        summary: {
          scope: repo ?? "All repositories",
          totalSessions: 0,
          running: 0,
          failed: 0,
          spendUsd: 0,
          tokens: 0,
        },
        running: [],
        recent: [],
        generatedAt: new Date().toISOString(),
      }
      return HttpResponse.json(empty)
    }

    return HttpResponse.json(buildDashboard(repo, limit))
  }),

  http.post(`${API_BASE}/reviews/preflight`, async ({ request }) => {
    await latency(request)
    if (scenarioOf(request) === "error") {
      return errorResponse(
        500,
        "preflight_unavailable",
        "Pre-flight could not run.",
        "The provider credential check did not respond.",
      )
    }
    const body = (await request.json()) as { prUrls?: string[] }
    const result = resolvePreflight(body.prUrls ?? [])
    return HttpResponse.json(result)
  }),

  http.post(`${API_BASE}/sessions`, async ({ request }) => {
    await latency(request)
    if (scenarioOf(request) === "error") {
      return errorResponse(
        500,
        "session_create_failed",
        "Could not start the review session.",
      )
    }
    const body = (await request.json()) as {
      prUrls?: string[]
      prompt?: string
      preset?: string
      attachments?: unknown[]
    }
    const prUrls = (body.prUrls ?? []).map((value) => value.trim()).filter(Boolean)
    if (prUrls.length === 0) {
      return errorResponse(422, "no_targets", "Add at least one pull request.")
    }
    const preflight = resolvePreflight(prUrls)
    if (preflight.valid.length === 0) {
      return errorResponse(
        422,
        "no_valid_targets",
        "None of the pasted links belong to a covered repository.",
      )
    }

    const session: CreatedSession = {
      id: `ses_${Date.now().toString(36)}${Math.floor(Math.random() * 1e4)
        .toString(36)
        .padStart(3, "0")}`,
      title: reviewTitleFromTitle(preflight.valid[0].title),
      name: buildSessionName(preflight.valid.map((item) => item.url)),
      status: "queued",
      model: MODEL_CATALOG.defaultModelId,
      provider: MODEL_CATALOG.defaultProvider,
      createdAt: new Date().toISOString(),
      targetCount: preflight.valid.length,
      prompt: body.prompt?.trim() || undefined,
    }
    return HttpResponse.json(session, { status: 201 })
  }),

  http.patch(`${API_BASE}/sessions/:id`, async ({ request, params }) => {
    await latency(request)
    if (scenarioOf(request) === "error") {
      return errorResponse(
        500,
        "session_rename_failed",
        "Could not rename the review session.",
      )
    }

    const id = String(params.id)
    const session = dataset.sessions.find((item) => item.id === id)
    if (!session) {
      return errorResponse(
        404,
        "session_not_found",
        `Session ${id} does not exist.`,
      )
    }

    const body = (await request.json()) as { title?: string }
    const title = body.title?.trim()
    if (!title) {
      return errorResponse(422, "title_required", "Provide a non-empty title.")
    }

    session.title = title
    return HttpResponse.json(session)
  }),
]
