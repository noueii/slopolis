/**
 * MSW handlers for the pull-request inbox (spec v3 §1–§3) and the surfaces
 * around it: the repository switches, the dock's pre-flight and submission
 * calls, and the model and preset catalogs its selectors read.
 *
 * Serves the shapes in `src/api/contract.ts`; `default`/`empty`/`error`/`slow`
 * are driven by the `x-mock-scenario` header. `GET /api/pull-requests` has no
 * backend yet (spec v3 §7), so `inboxHandlers` stays registered when
 * `VITE_MOCK=off` and the staleness join is synthesized from a seeded reviewed
 * SHA — `session_targets` records no commit SHA to compare against.
 */

import { HttpResponse, delay, http } from "msw"

import type {
  ApiErrorBody,
  CreatedSession,
  FilterOption,
  ModelCatalog,
  OpenPullRequest,
  PrReference,
  PreflightResult,
  PullRequestChecks,
  PullRequestFilterOptions,
  PullRequestListItem,
  PullRequestListResponse,
  PullRequestReview,
  PullRequestReviewFilter,
  PullRequestSort,
  PullRequestSummary,
  RepositoryRef,
  RepositorySummary,
  RepositoryUpdate,
  ReviewPresetCatalog,
  ReviewSession,
  SessionTarget,
  TargetStatus,
  UserRef,
} from "@/api/contract"
import {
  PR_TITLES,
  REPOS,
  USERS,
  headBranchFromTitle,
  reviewTitleFromTitle,
} from "./data"
import { MODEL_CATALOG, REVIEW_PRESET_CATALOG, dataset } from "./dataset"

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "/api"

const PR_URL_PATTERN =
  /^https?:\/\/github\.com\/([^/\s]+)\/([^/\s]+)\/pull\/(\d+)/i

/** The harness steps a running review walks through, in order. */
const STEPS = [
  "Fetching pull request metadata",
  "Snapshotting repository at head",
  "Chunking diff and surrounding context",
  "Scanning changed files for defects",
  "Cross-checking repository conventions",
  "Ranking and drafting findings",
  "Publishing review comments",
]

const HOUR = 60 * 60 * 1000
const DAY = 24 * HOUR

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

/** A deterministic draw for one field of one pull request. */
function pick(
  fullName: string,
  number: number,
  salt: string,
  mod: number,
): number {
  return hash(`${fullName}#${number}:${salt}`) % mod
}

/** A deterministic 40-hex commit id; the mock has no real commit graph. */
function commitSha(seed: string): string {
  let out = ""
  for (let index = 0; out.length < 40; index += 1) {
    out += hash(`${seed}:${index}`).toString(16).padStart(8, "0")
  }
  return out.slice(0, 40)
}

/* -------------------------------------------------------------------------
 * Repository switch (spec 10.1)
 * ---------------------------------------------------------------------- */

/**
 * Repositories the workspace has switched off (spec 10.1). They stay listed and
 * keep their history; pre-flight refuses their pull requests. Seeded so the
 * parked state is visible without clicking anything, and mutable so the mock's
 * PATCH behaves like the real switch.
 */
const parkedRepositories = new Set(
  REPOS.filter((_repo, index) => index % 5 === 2).map((repo) => repo.fullName),
)

/**
 * A repository the inbox may serve and pre-flight may accept: the GitHub App
 * can still read it and the workspace has not parked it. The mock's
 * installation never loses access, so `enabled` is the switch that moves.
 */
function isAvailable(fullName: string): boolean {
  return !parkedRepositories.has(fullName)
}

function buildRepositories(now = Date.now()): RepositorySummary[] {
  return REPOS.map((repo, index) => ({
    id: repoId(repo.fullName),
    fullName: repo.fullName,
    private: repo.isPrivate,
    defaultBranch: index % 5 === 0 ? "develop" : "main",
    openPrCount: OPEN_PULL_REQUESTS.get(repo.fullName)?.length ?? 0,
    lastActivityAt: new Date(now - (index + 1) * (37 * 60 * 1000)).toISOString(),
    connected: true,
    enabled: !parkedRepositories.has(repo.fullName),
  }))
}

/* -------------------------------------------------------------------------
 * Open pull requests: the inbox's source rows
 * ---------------------------------------------------------------------- */

/** One open pull request plus the fields the wire shape has no room for. */
interface SourcePullRequest {
  pull: OpenPullRequest
  /** `created_desc` sorts on this; the contract carries no creation time. */
  createdAt: string
}

function repositoryRef(fullName: string, isPrivate: boolean): RepositoryRef {
  return {
    id: repoId(fullName),
    fullName,
    private: isPrivate,
    defaultBranch: "main",
  }
}

function authorFor(fullName: string, number: number): UserRef {
  const user = USERS[pick(fullName, number, "author", USERS.length)]
  return {
    id: `usr_${user.handle}`,
    handle: user.handle,
    name: user.name,
    isAdmin: user.handle === "noueii",
  }
}

/**
 * Everything about a row that does not come from the sessions: diff size, CI
 * rollup, author, head commit. Drawn from the row's identity alone, so a pull
 * request reads the same whichever repository listing surfaced it.
 */
function sourcePull(
  repo: { fullName: string; isPrivate: boolean },
  number: number,
  title: string,
  headBranch: string,
  now: number,
): SourcePullRequest {
  const { fullName, isPrivate } = repo
  const id = `pr_${fullName.replace(/[^a-z0-9]/gi, "_")}_${number}`
  const total = 2 + pick(fullName, number, "total", 5)
  const roll = pick(fullName, number, "roll", 100)
  const passing =
    roll < 62 ? total : roll < 82 ? Math.max(1, Math.floor(total / 2)) : 0
  const state: PullRequestChecks["state"] =
    passing === 0 ? "failing" : passing >= total ? "passing" : "pending"

  return {
    pull: {
      id,
      repository: repositoryRef(fullName, isPrivate),
      number,
      title,
      url: `https://github.com/${fullName}/pull/${number}`,
      author: authorFor(fullName, number),
      headBranch,
      headSha: commitSha(`${id}:head`),
      updatedAt: new Date(
        now - (12 + pick(fullName, number, "updated", 72 * 60)) * 60 * 1000,
      ).toISOString(),
      draft: pick(fullName, number, "draft", 9) === 0,
      comments: pick(fullName, number, "comments", 24),
      changedFiles: 1 + pick(fullName, number, "files", 42),
      additions: 4 + pick(fullName, number, "additions", 480),
      deletions: pick(fullName, number, "deletions", 220),
      checks: { state, total, passing },
    },
    createdAt: new Date(
      now - (1 + pick(fullName, number, "created", 45)) * DAY,
    ).toISOString(),
  }
}

/**
 * The workspace's open pull requests, keyed by repository — the rows the inbox
 * filters and pages over.
 *
 * Every session target the dataset carries contributes a row, so the review
 * column shows the work the Sessions screen already knows about; the newest
 * target for a number supplies the row's title and branch. The rest are
 * invented, so the list also has never-reviewed work to filter on. Built once,
 * like the sessions themselves; parking is applied per request.
 */
function buildOpenPullRequests(
  now = Date.now(),
): Map<string, SourcePullRequest[]> {
  const seeded = new Map<
    string,
    { target: SessionTarget; createdAt: number }
  >()
  for (const session of dataset.sessions) {
    const createdAt = new Date(session.createdAt).getTime()
    for (const target of session.targets) {
      const key = `${target.repository.fullName}#${target.number}`
      const seen = seeded.get(key)
      if (!seen || createdAt > seen.createdAt) seeded.set(key, { target, createdAt })
    }
  }

  const byRepo = new Map<string, SourcePullRequest[]>()

  for (const repo of REPOS) {
    const rows: SourcePullRequest[] = []
    const numbers = new Set<number>()

    for (const { target } of seeded.values()) {
      if (target.repository.fullName !== repo.fullName) continue
      numbers.add(target.number)
      rows.push(
        sourcePull(repo, target.number, target.title, target.headBranch, now),
      )
    }

    const filler = 3 + (hash(`${repo.fullName}:count`) % 7)
    for (let index = 0; index < filler; index += 1) {
      let number = 40 + (hash(`${repo.fullName}:open:${index}`) % 900)
      while (numbers.has(number)) number = 40 + ((number - 40 + 1) % 900)
      numbers.add(number)

      const title =
        PR_TITLES[
          hash(`${repo.fullName}:open:${index}:title`) % PR_TITLES.length
        ]
      rows.push(
        sourcePull(repo, number, title, headBranchFromTitle(title), now),
      )
    }

    byRepo.set(repo.fullName, rows)
  }

  return byRepo
}

const OPEN_PULL_REQUESTS = buildOpenPullRequests()

const REPOSITORY_INDEX = new Map(
  REPOS.map((repo) => [repo.fullName.toLowerCase(), repo]),
)

/** Every open pull request in a connected, enabled repository. */
function availableSources(): SourcePullRequest[] {
  const rows: SourcePullRequest[] = []
  for (const repo of REPOS) {
    if (!isAvailable(repo.fullName)) continue
    rows.push(...(OPEN_PULL_REQUESTS.get(repo.fullName) ?? []))
  }
  return rows
}

/* -------------------------------------------------------------------------
 * The review join (spec v3 §2)
 * ---------------------------------------------------------------------- */

const IN_FLIGHT_TARGET: Partial<Record<TargetStatus, true>> = {
  queued: true,
  running: true,
}

const NEVER_REVIEWED: PullRequestReview = {
  state: "never",
  sessionId: null,
  reviewedSha: null,
  commitsSinceReview: 0,
  findingsCount: 0,
  progress: null,
  step: null,
  reviewedAt: null,
}

/**
 * How far behind the reviewed commit the PR head has moved. Seeded so the
 * stale state is visible without clicking anything — every third reviewed pull
 * request is behind its review by one to four commits, and a smaller subset is
 * behind by an unknown distance (`null`), which is what the real API reports
 * when the head moved but the comparison could not count the commits between
 * (spec v3 §2 / §7). `0` means the review is current.
 */
function stalenessDraw(pull: OpenPullRequest): number | null {
  const seed = hash(`${pull.id}:staleness`)
  if (seed % 11 === 0) return null
  return seed % 3 === 0 ? 1 + (Math.floor(seed / 3) % 4) : 0
}

/** At least this far behind: an unknown distance still moved the head once. */
function commitsBehind(review: PullRequestReview): number {
  return review.commitsSinceReview ?? 1
}

/**
 * The session target that decides a pull request's review state: the most
 * recent one covering `(repository, number)`, except that a target still in
 * flight outranks an older completed one (spec v3 §2).
 */
function latestTargetFor(
  fullName: string,
  number: number,
): { session: ReviewSession; target: SessionTarget } | null {
  const matches: Array<{ session: ReviewSession; target: SessionTarget }> = []
  for (const session of dataset.sessions) {
    for (const target of session.targets) {
      if (target.repository.fullName !== fullName) continue
      if (target.number !== number) continue
      matches.push({ session, target })
    }
  }
  if (matches.length === 0) return null

  const inFlight = matches.filter((match) => IN_FLIGHT_TARGET[match.target.status])
  const pool = inFlight.length > 0 ? inFlight : matches
  return pool.reduce((best, candidate) =>
    new Date(candidate.session.createdAt).getTime() >
    new Date(best.session.createdAt).getTime()
      ? candidate
      : best,
  )
}

function reviewFor(pull: OpenPullRequest): PullRequestReview {
  const match = latestTargetFor(pull.repository.fullName, pull.number)
  if (!match) return NEVER_REVIEWED
  const { session, target } = match

  if (target.status === "queued" || target.status === "running") {
    // The step phrasing the running review walks through, so the row reads like
    // the harness it mirrors.
    const seed = hash(session.id)
    const progress = target.status === "running" ? 16 + (seed % 66) : seed % 8
    const stepIndex = Math.min(
      STEPS.length - 1,
      Math.floor((progress / 100) * STEPS.length),
    )
    return {
      state: target.status,
      sessionId: session.id,
      reviewedSha: null,
      commitsSinceReview: 0,
      findingsCount: target.findingsCount,
      progress,
      step: STEPS[stepIndex],
      reviewedAt: null,
    }
  }

  if (target.status !== "done") {
    // failed / cancelled / skipped: the attempt produced no review, so the row
    // offers the session and a retry rather than a result.
    return {
      state: "failed",
      sessionId: session.id,
      reviewedSha: null,
      commitsSinceReview: 0,
      findingsCount: target.findingsCount,
      progress: null,
      step: null,
      reviewedAt: session.finishedAt ?? session.createdAt,
    }
  }

  const behind = stalenessDraw(pull)
  const untracked = untrackedDraw(pull)
  return {
    state: "reviewed",
    sessionId: session.id,
    // A review that predates commit tracking (spec v3 §2): the app knows it
    // happened and nothing about the commit it read, so it claims no distance.
    reviewedSha: untracked ? null : reviewedShaFor(pull, behind),
    commitsSinceReview: untracked ? null : behind,
    findingsCount: target.findingsCount,
    progress: null,
    step: null,
    reviewedAt: session.finishedAt ?? session.createdAt,
  }
}

/** A review at the head SHA is current; a stale one covered an earlier commit. */
function reviewedShaFor(pull: OpenPullRequest, behind: number | null): string {
  return behind === 0 ? pull.headSha : commitSha(`${pull.id}:reviewed`)
}

/**
 * Seeded so the untracked shape is visible: targets reviewed before
 * `reviewed_sha` existed have no commit recorded, which is neither current nor a
 * known distance behind (spec v3 §2 / §7).
 */
function untrackedDraw(pull: OpenPullRequest): boolean {
  return hash(`${pull.id}:tracking`) % 13 === 0
}

/**
 * Worth looking at again: the head moved past the review — by a known distance,
 * an unknown one, or one the review never recorded.
 */
function isStale(review: PullRequestReview): boolean {
  if (review.state !== "reviewed") return false
  return (
    review.reviewedSha === null ||
    review.commitsSinceReview === null ||
    review.commitsSinceReview > 0
  )
}

/* -------------------------------------------------------------------------
 * Filtering, sorting and paging (spec v3 §3)
 * ---------------------------------------------------------------------- */

const REVIEW_FILTERS: Array<{
  value: PullRequestReviewFilter
  label: string
}> = [
  { value: "never", label: "Never reviewed" },
  { value: "queued", label: "Queued" },
  { value: "running", label: "Running" },
  { value: "reviewed", label: "Reviewed" },
  { value: "stale", label: "Stale" },
  { value: "failed", label: "Failed" },
]

const CHECK_STATES: Array<PullRequestChecks["state"]> = [
  "passing",
  "failing",
  "pending",
  "none",
]

const CHECK_LABELS: Record<PullRequestChecks["state"], string> = {
  passing: "Passing",
  failing: "Failing",
  pending: "Pending",
  none: "None",
}

/** The whole inbox, review column joined, in repository order. */
function listInbox(): PullRequestListItem[] {
  return availableSources().map(({ pull }) => ({
    ...pull,
    review: reviewFor(pull),
  }))
}

function matchesQuery(row: PullRequestListItem, q: string): boolean {
  const needle = q.toLowerCase()
  const haystacks = [
    row.title,
    row.repository.fullName,
    `#${row.number}`,
    `${row.repository.fullName}#${row.number}`,
    row.author.handle,
  ]
  return haystacks.some((value) => value.toLowerCase().includes(needle))
}

function matchesReview(
  row: PullRequestListItem,
  filter: PullRequestReviewFilter,
): boolean {
  // `stale` refines `reviewed`: it selects the reviews that are behind the PR
  // head, while `reviewed` selects the state (spec v3 §2/§3).
  if (filter === "stale") return isStale(row.review)
  return row.review.state === filter
}

function updatedAtOf(row: PullRequestListItem): number {
  return new Date(row.updatedAt).getTime()
}

/**
 * Row order for `staleness_desc`: reviewed work leads with the reviews furthest
 * behind, then the attempts that need a retry or are in flight, then the pull
 * requests nobody has looked at.
 */
function stalenessRank(row: PullRequestListItem): number {
  if (row.review.state === "reviewed") return 0
  if (row.review.state === "never") return 2
  return 1
}

function compareRows(
  sort: PullRequestSort,
  a: PullRequestListItem,
  b: PullRequestListItem,
): number {
  switch (sort) {
    case "size_desc":
      return (
        b.additions +
          b.deletions -
          (a.additions + a.deletions) || updatedAtOf(b) - updatedAtOf(a)
      )
    case "staleness_desc":
      return (
        stalenessRank(a) - stalenessRank(b) ||
        commitsBehind(b.review) - commitsBehind(a.review) ||
        updatedAtOf(b) - updatedAtOf(a)
      )
    case "created_desc":
      return (
        (CREATED_AT_BY_ID.get(b.id) ?? 0) -
          (CREATED_AT_BY_ID.get(a.id) ?? 0) || updatedAtOf(b) - updatedAtOf(a)
      )
    case "updated_desc":
    default:
      return updatedAtOf(b) - updatedAtOf(a)
  }
}

/** Wire-shaped rows carry no creation time, so it rides alongside the join. */
const CREATED_AT_BY_ID = new Map<string, number>(
  availableSources().map(({ pull, createdAt }): [string, number] => [
    pull.id,
    new Date(createdAt).getTime(),
  ]),
)

function summarize(rows: PullRequestListItem[]): PullRequestSummary {
  let needsReview = 0
  let stale = 0
  let running = 0
  for (const row of rows) {
    const state = row.review.state
    if (state === "never" || isStale(row.review)) needsReview += 1
    if (isStale(row.review)) stale += 1
    if (state === "queued" || state === "running") running += 1
  }
  return { total: rows.length, needsReview, stale, running }
}

/**
 * The filter bar's options, counted over the whole connected-and-enabled set
 * (drafts included) rather than over the page or the active filters, so the bar
 * can distinguish "nothing open" from "nothing matches".
 */
function buildFilterOptions(
  rows: PullRequestListItem[],
): PullRequestFilterOptions {
  const repositories = new Map<string, number>()
  for (const repo of REPOS) {
    if (isAvailable(repo.fullName)) repositories.set(repo.fullName, 0)
  }
  const reviews = new Map<string, number>(
    REVIEW_FILTERS.map(({ value }): [string, number] => [value, 0]),
  )
  const checks = new Map<string, number>(
    CHECK_STATES.map((state): [string, number] => [state, 0]),
  )

  for (const row of rows) {
    const fullName = row.repository.fullName
    repositories.set(fullName, (repositories.get(fullName) ?? 0) + 1)
    reviews.set(row.review.state, (reviews.get(row.review.state) ?? 0) + 1)
    // `stale` counts the reviewed rows that are behind, on top of `reviewed`.
    if (isStale(row.review)) reviews.set("stale", (reviews.get("stale") ?? 0) + 1)
    checks.set(row.checks.state, (checks.get(row.checks.state) ?? 0) + 1)
  }

  const option = (value: string, label: string, hint: number): FilterOption => ({
    value,
    label,
    hint: `${hint}`,
  })

  return {
    repositories: [...repositories.entries()].map(([fullName, count]) =>
      option(fullName, fullName, count),
    ),
    reviews: REVIEW_FILTERS.map(({ value, label }) =>
      option(value, label, reviews.get(value) ?? 0),
    ),
    checks: CHECK_STATES.map((state) =>
      option(state, CHECK_LABELS[state], checks.get(state) ?? 0),
    ),
  }
}

/* -------------------------------------------------------------------------
 * Pre-flight and session naming (the dock's submission path)
 * ---------------------------------------------------------------------- */

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

    if (!isAvailable(repo.fullName)) {
      invalid.push(parsed.url)
      notices.push(
        `Repository ${repo.fullName} is disabled in slopolis, so its pull requests are not reviewed. Enable it under Repositories to review them again.`,
      )
      continue
    }

    const generated = OPEN_PULL_REQUESTS.get(repo.fullName)?.find(
      (row) => row.pull.number === parsed.number,
    )

    valid.push({
      url: parsed.url,
      repository:
        generated?.pull.repository ??
        repositoryRef(repo.fullName, repo.isPrivate),
      number: parsed.number,
      title:
        generated?.pull.title ??
        PR_TITLES[hash(parsed.url) % PR_TITLES.length],
    })
  }

  return {
    valid,
    invalid,
    notices:
      duplicates > 0
        ? [...notices, `${duplicates} duplicate link${duplicates === 1 ? "" : "s"} removed.`]
        : notices,
  }
}

function buildSessionName(prUrls: string[], now = new Date()): string {
  const first = normalizePrUrl(prUrls[0])
  const lead = first ? `${first.fullName}#${first.number}` : "New review"
  const extra = prUrls.length > 1 ? ` +${prUrls.length - 1} more` : ""
  return `${lead}${extra} — ${MONTHS[now.getMonth()]} ${now.getDate()}`
}

/* -------------------------------------------------------------------------
 * Handlers
 * ---------------------------------------------------------------------- */

/**
 * `GET /api/pull-requests` (spec v3 §6). Has no backend yet — spec v3 §7 has
 * the endpoint living in the mock alone — so `handlers.ts` keeps this array
 * registered even under `VITE_MOCK=off`, next to the templates precedent.
 */
export const inboxHandlers = [
  http.get(`${API_BASE}/pull-requests`, async ({ request }) => {
    await latency(request)
    if (scenarioOf(request) === "error") {
      return errorResponse(
        500,
        "pull_requests_unavailable",
        "Could not load the pull-request inbox.",
        "The GitHub App installation listing timed out.",
      )
    }

    const params = new URL(request.url).searchParams
    const page = Math.max(1, Number(params.get("page") ?? "1") || 1)
    const pageSize = Math.min(
      100,
      Math.max(1, Number(params.get("pageSize") ?? "25") || 25),
    )
    const q = params.get("q")?.trim() ?? ""
    const repo = params.get("repo") ?? ""
    const review = (params.get("review") ?? "") as PullRequestReviewFilter | ""
    const checks = (params.get("checks") ?? "") as
      | PullRequestChecks["state"]
      | ""
    const includeDrafts = params.get("drafts") === "1"
    const sort = (params.get("sort") ?? "updated_desc") as PullRequestSort

    const all = listInbox()
    const filterOptions = buildFilterOptions(all)

    // "Nothing open" is the filter bar's job to report, so the empty scenario
    // zeroes the rows and the summary but keeps the options populated.
    if (scenarioOf(request) === "empty") {
      const body: PullRequestListResponse = {
        items: [],
        page,
        pageSize,
        total: 0,
        totalPages: 0,
        summary: { total: 0, needsReview: 0, stale: 0, running: 0 },
        filterOptions,
        generatedAt: new Date().toISOString(),
      }
      return HttpResponse.json(body)
    }

    const filtered = all.filter((row) => {
      if (!includeDrafts && row.draft) return false
      if (q && !matchesQuery(row, q)) return false
      if (repo && row.repository.fullName !== repo) return false
      if (review && !matchesReview(row, review)) return false
      if (checks && row.checks.state !== checks) return false
      return true
    })

    const sorted = [...filtered].sort((a, b) => compareRows(sort, a, b))
    const total = sorted.length
    const totalPages = total === 0 ? 0 : Math.ceil(total / pageSize)
    const start = (page - 1) * pageSize

    const body: PullRequestListResponse = {
      items: sorted.slice(start, start + pageSize),
      page,
      pageSize,
      total,
      totalPages,
      summary: summarize(filtered),
      filterOptions,
      generatedAt: new Date().toISOString(),
    }
    return HttpResponse.json(body)
  }),
]

/** The repository switches the pre-flight refusal is read off (spec 10.1). */
export const repositoryHandlers = [
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
    if (typeof body.enabled !== "boolean") {
      return errorResponse(
        422,
        "validation_error",
        "Provide the parked state to set the repository to as a boolean: `enabled` is required.",
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
    if (body.enabled) {
      parkedRepositories.delete(repository.fullName)
    } else {
      parkedRepositories.add(repository.fullName)
    }
    // The parked state is read back out of the store, so the response is what a
    // later GET serves rather than what this request happened to send.
    const updated =
      buildRepositories().find((item) => item.id === repository.id) ??
      repository
    return HttpResponse.json(updated)
  }),
]

/**
 * The dock's submission path plus the catalogs its selectors read — the
 * `prUrls`/`prompt`/`preset` pair spec v3 §6 leaves unchanged.
 */
export const dockHandlers = [
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
