/**
 * Deterministic mock dataset.
 *
 * Everything here is generated from a fixed seed so the mock is stable across
 * reloads (important for design review and screenshots). This module is only
 * imported by the MSW layer.
 */

import type {
  Finding,
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

/**
 * Findings the mock serves. The pools stay small so every shape a finding
 * takes is visible in the dataset, and are grouped by category so a message
 * never reads off-topic beside its chip.
 */
const FINDING_PATHS = [
  "src/queue/worker.py",
  "src/auth/tokens.py",
  "src/api/sessions.py",
  "src/db/session_repository.py",
  "apps/worker/slopolis_worker/review_target.py",
  "packages/core/slopolis_core/publish/github.py",
  "apps/web/src/features/sessions/SessionDetail.tsx",
  "tests/e2e/test_review_flow.py",
]

const FINDING_MESSAGES: Record<string, string[]> = {
  security: [
    "The webhook body is parsed before the signature is checked, so a forged delivery can requeue a target.",
    "The token is compared with `==`, which leaks its length through timing.",
  ],
  performance: [
    "Each finding opens its own session; the loop issues one query per row.",
    "The diff is re-read for every sub-agent instead of being loaded once per target.",
  ],
  correctness: [
    "`finishedAt` is set from the start time, so a retried session reports the failed attempt's duration.",
    "The page count is computed from the unpaged rows, so the last page is one short.",
  ],
  reliability: [
    "A transient provider error ends the target instead of being retried.",
    "The stream is closed without a final frame, so a client that reconnects loses the last status.",
  ],
  maintainability: [
    "This branch duplicates the retry rules the queue already owns.",
    "The helper takes five positional booleans; a caller can transpose two of them silently.",
  ],
  tests: [
    "The retry path is only covered through the happy case, so the refusal is untested.",
    "The fixture pins the summary wording instead of the behavior it stands for.",
  ],
}

const FINDING_CATEGORIES = Object.keys(FINDING_MESSAGES)

/**
 * Literal replacement code, kept with the category it answers so a suggestion
 * always reads as a fix for the message above it.
 */
const FINDING_SUGGESTIONS: Record<string, string[]> = {
  security: [
    "if not hmac.compare_digest(signature, expected):\n    raise SignatureError(\"signature mismatch\")",
  ],
  performance: [
    "rows = await session.execute(\n    select(Finding).where(Finding.target_id.in_(target_ids))\n)",
  ],
  correctness: [
    "finished_at = started_at + timedelta(milliseconds=duration_ms)",
  ],
  reliability: [
    "for attempt in range(MAX_ATTEMPTS):\n    try:\n        return await provider.complete(prompt)\n    except TransientError:\n        await asyncio.sleep(backoff(attempt))",
  ],
  maintainability: [
    "def retryable(target: SessionTarget) -> bool:\n    return target.status in RETRYABLE_STATUSES",
  ],
  tests: [
    "with pytest.raises(RetryRefused):\n    await retry(session, target_ids=[running.id])",
  ],
}

/** Most findings are worth reading; few stop a merge on their own. */
const FINDING_SEVERITY_WEIGHTS: Array<[Severity, number]> = [
  ["info", 0.3],
  ["warning", 0.34],
  ["error", 0.24],
  ["critical", 0.12],
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

export function headBranchFromTitle(title: string): string {
  const match = title.match(/^([a-z]+)(?:\([^)]*\))?:\s*(.+)$/i)
  const type = (match?.[1] ?? "feat").toLowerCase()
  const subject = match?.[2] ?? title
  const slug = subject
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
  return slug ? `${type}/${slug}` : type
}

export function reviewTitleFromTitle(title: string): string {
  const match = title.match(/^[a-z]+(?:\([^)]*\))?:\s*(.+)$/i)
  const subject = match?.[1] ?? title
  const phrase = subject
    .split(/\s+/)
    .slice(0, 6)
    .join(" ")
    .replace(/[.,;:!?]+$/, "")
  return phrase ? phrase.charAt(0).toUpperCase() + phrase.slice(1) : title
}

const HOUR = 60 * 60 * 1000
const DAY = 24 * HOUR

/** The session the dataset seeds as a retry in flight. */
export const RETRY_SESSION_ID = "ses_0000retry"

/**
 * What that session's superseded attempt ended as.
 *
 * A real deployment keeps the runs a failed attempt left when the queue picks
 * the session up again, so the run tree still serves them; the mock builds its
 * trees from the session's current state alone and cannot recover them. Without
 * this, a queued session would always show a blank pending tree, and the
 * "queued header over failed runs" state would not be reachable in
 * `make dev-mock`.
 */
export const RETRY_SESSION_ATTEMPT: {
  session: SessionStatus
  target: TargetStatus
} = { session: "failed", target: "failed" }

/**
 * The dataset's retry in flight: a review that failed — its main orchestrator
 * and both targets — and was put back on the queue. Its targets are `queued`
 * again (spec 10.5 requeues what it retries) while the usage and findings they
 * carry are the failed attempt's, which is the pair the run tree has to read as
 * history rather than as the current result.
 */
function retriedSession(now: number): ReviewSession {
  const model = MODELS[2]
  const createdMs = now - 3 * 60 * 1000
  const repos = [REPOS[0], REPOS[3]]
  const targets: SessionTarget[] = repos.map((repo, index) => {
    const number = 900 + index * 7
    const title = PR_TITLES[index]
    const tokens = 14_200 - index * 3_400
    return {
      id: `tgt_retry_${index}`,
      repository: {
        id: `repo_${repo.fullName.replace(/[^a-z0-9]/gi, "_")}`,
        fullName: repo.fullName,
        private: repo.isPrivate,
        defaultBranch: "main",
      } satisfies RepositoryRef,
      number,
      title,
      url: `https://github.com/${repo.fullName}/pull/${number}`,
      headBranch: headBranchFromTitle(title),
      status: "queued",
      // Requeued by the retry, so not a retry candidate again until it settles.
      retryAction: null,
      findingsCount: index === 0 ? 2 : 0,
      tokens,
      costUsd: Number(((tokens / 1000) * model.pricePer1k).toFixed(4)),
    }
  })
  const tokens = targets.reduce((sum, target) => sum + target.tokens, 0)
  const findingsCount = targets.reduce(
    (sum, target) => sum + target.findingsCount,
    0,
  )
  return {
    id: RETRY_SESSION_ID,
    title: reviewTitleFromTitle(targets[0].title),
    name: `${targets[0].repository.fullName}#${targets[0].number} +1 more — ${formatShortDate(new Date(createdMs))}`,
    status: "queued",
    model: model.id,
    provider: model.provider,
    triggeredBy: {
      id: "usr_noueii",
      handle: "noueii",
      name: "Noah Yu",
      avatarUrl: undefined,
      isAdmin: true,
    },
    createdAt: new Date(createdMs).toISOString(),
    // The failed attempt's start is still on the row; the retry cleared the
    // finish time, which is what makes the session read as queued again.
    startedAt: new Date(createdMs + 4_000).toISOString(),
    targets,
    targetCount: targets.length,
    tokens,
    costUsd: Number(
      targets.reduce((sum, target) => sum + target.costUsd, 0).toFixed(4),
    ),
    findingsCount,
    prompt: PROMPTS[3],
  }
}

export interface MockDataset {
  sessions: ReviewSession[]
  /** A target's findings, keyed by target id (the detail read's payload). */
  findings: Record<string, Finding[]>
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
      // One draw, taken for the same targets as before: a failed target whose
      // model never reported usage is one the retry has to review again.
      const usageDraw =
        targetStatus === "running" || targetStatus === "failed" ? rand() : 0
      const doneRatio =
        targetStatus === "done"
          ? 1
          : targetStatus === "running"
            ? 0.35 + usageDraw * 0.4
            : targetStatus === "failed"
              ? usageDraw < 0.35
                ? 0
                : 0.4 + usageDraw * 0.5
              : 0
      const tokens = Math.round(baseTokens * doneRatio)
      const costUsd = Number(((tokens / 1000) * model.pricePer1k).toFixed(4))
      const drawnFindings =
        targetStatus === "done"
          ? Math.floor(rand() * 14)
          : Math.floor(rand() * 4)
      const findingsCount =
        targetStatus === "failed" && tokens === 0 ? 0 : drawnFindings
      // The retry action the server computes from the last attempt: a cancelled
      // target never published anything either, so its retry is a review.
      const retryAction: SessionTarget["retryAction"] =
        targetStatus === "failed"
          ? tokens > 0
            ? "publish"
            : "review"
          : targetStatus === "cancelled"
            ? "review"
            : null

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
        retryAction,
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
      title: reviewTitleFromTitle(first.title),
      name,
      status,
      model: model.id,
      provider: model.provider,
      triggeredBy: {
        id: `usr_${user.handle}`,
        handle: user.handle,
        name: user.name,
        avatarUrl: undefined,
        isAdmin: user.handle === "noueii",
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

  // One seeded retry in flight, so `make dev-mock` can show a queued session
  // whose runs are the failed attempt's without a hand-rolled API call.
  sessions.push(retriedSession(now))

  sessions.sort(
    (a, b) => new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime(),
  )
  return {
    sessions,
    findings: buildFindings(sessions),
    generatedAt: now,
    filterOptions: buildFilterOptions(sessions),
    stats: buildStats(sessions),
  }
}

/**
 * The findings every target carries, built in a pass of its own: the session
 * loop's draw sequence is what the rest of the mock stack joins against, so
 * nothing here may draw from its generator.
 */
function buildFindings(sessions: ReviewSession[]): Record<string, Finding[]> {
  const rand = mulberry32(0x4f1d1a95)
  const findings: Record<string, Finding[]> = {}

  for (const session of sessions) {
    for (const target of session.targets) {
      findings[target.id] = Array.from(
        { length: target.findingsCount },
        (_, index) => {
          const category =
            FINDING_CATEGORIES[Math.floor(rand() * FINDING_CATEGORIES.length)]
          const messages = FINDING_MESSAGES[category]
          const suggestions = FINDING_SUGGESTIONS[category]
          const severity = pickWeighted(rand, FINDING_SEVERITY_WEIGHTS)
          // A minority cite no line. Those can only ever be summarised, which
          // is the case the UI has to render without a comment link.
          const line = rand() < 0.2 ? null : 1 + Math.floor(rand() * 320)
          // Only a `done` target posted anything, and only what the
          // publisher's default `warning` threshold and a diff line let it
          // post inline; the rest went into the summary comment. The comment
          // link, the login it is posted as and the time it was written all
          // hang off that one answer, so they can never disagree.
          const posted = postedInline(target, severity, line)
          // Hoisted so the hunk can read them; the draws stay in the order
          // they were taken in.
          const path =
            FINDING_PATHS[Math.floor(rand() * FINDING_PATHS.length)]
          const message = messages[Math.floor(rand() * messages.length)]
          const suggestion =
            rand() < 0.45
              ? suggestions[Math.floor(rand() * suggestions.length)]
              : null
          return {
            path,
            line,
            severity,
            category,
            message,
            suggestion,
            commentUrl: posted
              ? `https://github.com/${target.repository.fullName}/pull/${target.number}#discussion_r${commentId(target.id, index)}`
              : null,
            // The app's own login, read from its configured slug rather than
            // looked up — rendering a session costs no GitHub call.
            author: posted ? "slopolis-dev[bot]" : null,
            // The publisher stamps the comment's time in the same write that
            // records it, so a posted finding dates from the session's finish.
            postedAt: posted ? (session.finishedAt ?? session.createdAt) : null,
            diffHunk:
              posted && line !== null
                ? findingDiffHunk(path, line, suggestion)
                : null,
          } satisfies Finding
        },
      ).sort(bySeverityThenLocation)
    }
  }

  return findings
}

/**
 * The order the API promises (spec 10.8): most severe first, then file order.
 * The mock has to serve the same order the server does, or a design reviewed
 * against it would be reviewed against a list that does not exist.
 */
function bySeverityThenLocation(a: Finding, b: Finding): number {
  const severity =
    SEVERITY_ORDER.indexOf(b.severity) - SEVERITY_ORDER.indexOf(a.severity)
  if (severity !== 0) return severity
  if (a.path !== b.path) return a.path < b.path ? -1 : 1
  if ((a.line ?? 0) !== (b.line ?? 0)) return (a.line ?? 0) - (b.line ?? 0)
  if (a.message === b.message) return 0
  return a.message < b.message ? -1 : 1
}

const POSTING_THRESHOLD: Severity = "warning"

/** Whether the publisher put this finding on a diff line (spec 10.7). */
function postedInline(
  target: SessionTarget,
  severity: Severity,
  line: number | null,
): boolean {
  return (
    target.status === "done" &&
    line !== null &&
    SEVERITY_ORDER.indexOf(severity) >= SEVERITY_ORDER.indexOf(POSTING_THRESHOLD)
  )
}

/**
 * A GitHub comment id for a permalink. Derived from the target and the
 * finding's position rather than drawn, so a link stays stable however the
 * pools are reordered.
 */
function commentId(targetId: string, index: number): number {
  let hash = 0
  for (let i = 0; i < targetId.length; i += 1) {
    hash = (hash * 31 + targetId.charCodeAt(i)) | 0
  }
  return 2_400_000_000 + (Math.abs(hash) % 90_000) * 100 + index
}

/**
 * The code a generated hunk surrounds its change with. Deliberately generic:
 * the mock cites files across several languages, and the hunk only has to read
 * as the code around the finding. All of it sits one level in, so the added
 * line can join it without changing the block's shape.
 */
const HUNK_CONTEXT_LINES = [
  "    target = self._targets.get(target_id)",
  "    if target is None:",
  "        return None",
  "    started = time.monotonic()",
  "    await self._queue.put(target)",
  "    return TargetStatus.DONE",
  "    for attempt in range(MAX_ATTEMPTS):",
  "    session = await self._sessions.get(session_id)",
]

/**
 * The hunk GitHub prints above a posted comment: the `@@` header, a couple of
 * context lines, and a `+` line whose new line number is the finding's own —
 * so the card highlights the row the comment is anchored to. The suggestion is
 * the replacement for that line, so its first line is what the hunk adds.
 *
 * Derived from the finding rather than drawn from the dataset's generator,
 * whose sequence the rest of the mock joins against.
 */
function findingDiffHunk(
  path: string,
  line: number,
  suggestion: string | null,
): string {
  const base = (line * 7 + path.length) % HUNK_CONTEXT_LINES.length
  // A change near the top of the file has fewer lines above it, exactly as
  // GitHub prints it — the header still lands the added line on `line`.
  const lead = Math.min(2, line - 1)
  const before = [
    HUNK_CONTEXT_LINES[base],
    HUNK_CONTEXT_LINES[(base + 1) % HUNK_CONTEXT_LINES.length],
  ].slice(0, lead)
  const after = HUNK_CONTEXT_LINES[(base + 3) % HUNK_CONTEXT_LINES.length]
  const replacement = (suggestion ?? "return retryable(target)").split("\n")[0]
  // The replacement joins the block the context lines sit in.
  const added = `    ${replacement.trimStart()}`
  const start = line - before.length
  const context = before.length + 1
  const body = [
    ...before.map((text) => ` ${text}`),
    `+${added}`,
    ` ${after}`,
  ]

  return [
    `@@ -${start},${context} +${start},${context + 1} @@`,
    ...body,
  ].join("\n")
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
