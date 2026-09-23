/**
 * The pull-request inbox mock (spec v3 §1–§3) and the repository switches it
 * reads. These tests hold the handlers to what the app observes: which rows the
 * filters serve, what the review column says about them, and that parking a
 * repository takes its pull requests out of the list.
 */

import { setupServer } from "msw/node"
import { afterAll, beforeAll, describe, expect, it } from "vitest"

import type {
  ApiErrorBody,
  PullRequestListItem,
  PullRequestListResponse,
  RepositoryListResponse,
  RepositorySummary,
} from "@/api/contract"
import { RETRY_SESSION_ID } from "./data"
import { dataset } from "./dataset"
import { inboxHandlers, repositoryHandlers } from "./pullRequests"

const server = setupServer(...inboxHandlers, ...repositoryHandlers)

beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterAll(() => server.close())

function query(params: Record<string, string | number>): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    search.set(key, String(value))
  }
  const encoded = search.toString()
  return encoded ? `?${encoded}` : ""
}

/** One inbox page. Reads widen to the mock's largest page unless told otherwise. */
async function readPage(
  params: Record<string, string | number> = {},
): Promise<PullRequestListResponse> {
  const response = await fetch(
    `/api/pull-requests${query({ pageSize: 100, ...params })}`,
  )
  if (!response.ok) {
    throw new Error(
      `inbox read failed with ${response.status}: ${await response.text()}`,
    )
  }
  return (await response.json()) as PullRequestListResponse
}

/** Every row the filters serve, page by page. */
async function allRows(
  params: Record<string, string | number> = {},
): Promise<PullRequestListItem[]> {
  const first = await readPage(params)
  const rows = [...first.items]
  for (let page = 2; page <= first.totalPages; page += 1) {
    rows.push(...(await readPage({ ...params, page })).items)
  }
  return rows
}

async function listRepositories(): Promise<RepositorySummary[]> {
  const response = await fetch("/api/repositories")
  const body = (await response.json()) as RepositoryListResponse
  return body.items
}

async function patchRepository(id: string, body: unknown): Promise<Response> {
  return fetch(`/api/repositories/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  })
}

/** The repositories the workspace has left switched on. */
async function enabledRepositories(): Promise<Set<string>> {
  const items = await listRepositories()
  return new Set(
    items.filter((item) => item.enabled).map((item) => item.fullName),
  )
}

function prKey(fullName: string, number: number): string {
  return `${fullName}#${number}`
}

describe("pull-request inbox", () => {
  it("hides draft pull requests unless they are asked for", async () => {
    const visible = await allRows()
    const withDrafts = await allRows({ drafts: 1 })

    expect(visible.length).toBeGreaterThan(0)
    expect(visible.some((row) => row.draft)).toBe(false)

    const drafts = withDrafts.filter((row) => row.draft)
    expect(drafts.length).toBeGreaterThan(0)
    expect(withDrafts.length).toBe(visible.length + drafts.length)
  })

  it("serves never-reviewed rows with nothing joined onto them", async () => {
    const rows = await allRows({ review: "never" })

    expect(rows.length).toBeGreaterThan(0)
    for (const row of rows) {
      expect(row.review).toEqual({
        state: "never",
        sessionId: null,
        reviewedSha: null,
        commitsSinceReview: 0,
        findingsCount: 0,
        progress: null,
        step: null,
        reviewedAt: null,
      })
    }
  })

  it("reports a completed review from the session that owns the pull request", async () => {
    const enabled = await enabledRepositories()
    const covers = new Map<string, number>()
    for (const session of dataset.sessions) {
      for (const target of session.targets) {
        const key = prKey(target.repository.fullName, target.number)
        covers.set(key, (covers.get(key) ?? 0) + 1)
      }
    }
    const owner = dataset.sessions
      .flatMap((session) =>
        session.targets.map((target) => ({ sessionId: session.id, target })),
      )
      .find(
        ({ target }) =>
          target.status === "done" &&
          enabled.has(target.repository.fullName) &&
          covers.get(prKey(target.repository.fullName, target.number)) === 1,
      )
    expect(owner).toBeDefined()

    const rows = await allRows({ drafts: 1 })
    const row = rows.find(
      (item) =>
        item.repository.fullName === owner?.target.repository.fullName &&
        item.number === owner?.target.number,
    )
    expect(row?.review.state).toBe("reviewed")
    expect(row?.review.sessionId).toBe(owner?.sessionId)
    expect(row?.review.reviewedAt).not.toBeNull()
  })

  it("reports the review the retry session has in flight", async () => {
    const retry = dataset.sessions.find(
      (session) => session.id === RETRY_SESSION_ID,
    )
    const target = retry?.targets.find((item) => item.status === "queued")
    expect(target).toBeDefined()

    const rows = await allRows({ drafts: 1 })
    const row = rows.find(
      (item) =>
        item.repository.fullName === target?.repository.fullName &&
        item.number === target?.number,
    )
    expect(row?.review.state).toBe("queued")
    expect(row?.review.sessionId).toBe(RETRY_SESSION_ID)
    expect(row?.review.progress).not.toBeNull()
    expect(row?.review.step).not.toBeNull()
    expect(row?.review.reviewedSha).toBeNull()
  })

  it("selects exactly the reviewed rows that are behind their pull request", async () => {
    const rows = await allRows({ drafts: 1 })
    const behind = rows.filter(
      (row) =>
        row.review.state === "reviewed" &&
        (row.review.reviewedSha === null ||
          row.review.commitsSinceReview !== 0),
    )
    expect(behind.length).toBeGreaterThan(0)
    // An unknown distance is still behind, which is what `null` reports.
    expect(behind.some((row) => row.review.commitsSinceReview === null)).toBe(
      true,
    )
    // A review whose commit was never recorded is worth redoing too.
    expect(behind.some((row) => row.review.reviewedSha === null)).toBe(true)

    const selected = await allRows({ review: "stale", drafts: 1 })
    expect(selected.map((row) => row.id).sort()).toEqual(
      behind.map((row) => row.id).sort(),
    )
    for (const row of selected) {
      expect(row.review.state).toBe("reviewed")
      if (row.review.reviewedSha !== null) {
        expect(row.review.reviewedSha).not.toBe(row.headSha)
      }
      if (row.review.commitsSinceReview !== null) {
        expect(row.review.commitsSinceReview).toBeLessThanOrEqual(4)
      }
    }

    const reviewed = await allRows({ review: "reviewed", drafts: 1 })
    expect(reviewed.length).toBeGreaterThan(behind.length)
    for (const row of reviewed) {
      expect(row.review.state).toBe("reviewed")
      // A review at the head SHA has nothing behind it; one that is behind has
      // moved on from the SHA it covered, and one that never recorded a SHA
      // claims neither. All three say what the app knows, not what it assumes.
      if (row.review.reviewedSha === null) {
        expect(row.review.commitsSinceReview).toBeNull()
      } else if (row.review.commitsSinceReview === 0) {
        expect(row.review.reviewedSha).toBe(row.headSha)
      } else {
        expect(row.review.reviewedSha).not.toBe(row.headSha)
      }
    }
  })

  it("narrows the served rows by search, repository and checks", async () => {
    const rows = await allRows()
    const sample = rows[0]

    const byNumber = await allRows({ q: `#${sample.number}` })
    expect(byNumber.some((row) => row.id === sample.id)).toBe(true)
    expect(byNumber.every((row) => row.number === sample.number)).toBe(true)

    const byAuthor = await allRows({ q: sample.author.handle })
    expect(byAuthor.some((row) => row.id === sample.id)).toBe(true)

    const byRepo = await allRows({ repo: sample.repository.fullName })
    expect(byRepo.length).toBeGreaterThan(0)
    expect(byRepo.length).toBeLessThan(rows.length)
    expect(
      byRepo.every((row) => row.repository.fullName === sample.repository.fullName),
    ).toBe(true)

    const checks = sample.checks.state
    const byChecks = await allRows({ checks })
    expect(byChecks.length).toBeGreaterThan(0)
    expect(byChecks.every((row) => row.checks.state === checks)).toBe(true)
  })

  it("leads with the reviews furthest behind when sorted by staleness", async () => {
    const rows = await allRows({ sort: "staleness_desc", drafts: 1 })
    const reviewed = rows
      .map((row, index) => ({ row, index }))
      .filter(({ row }) => row.review.state === "reviewed")
      .map(({ index }) => index)
    const never = rows
      .map((row, index) => ({ row, index }))
      .filter(({ row }) => row.review.state === "never")
      .map(({ index }) => index)

    expect(reviewed.length).toBeGreaterThan(0)
    expect(never.length).toBeGreaterThan(0)
    expect(Math.max(...reviewed)).toBeLessThan(Math.min(...never))

    for (let index = 1; index < reviewed.length; index += 1) {
      // An unknown distance still moved the head once, so it ranks as one.
      const previous =
        rows[reviewed[index - 1]].review.commitsSinceReview ?? 1
      const current = rows[reviewed[index]].review.commitsSinceReview ?? 1
      expect(previous).toBeGreaterThanOrEqual(current)
    }
  })

  it("pages over the filtered set and keeps totals off the page", async () => {
    const rows = await allRows()
    const perRepo = new Map<string, number>()
    for (const row of rows) {
      perRepo.set(
        row.repository.fullName,
        (perRepo.get(row.repository.fullName) ?? 0) + 1,
      )
    }
    const repo = [...perRepo.entries()].find(([, count]) => count > 6)?.[0] ?? ""
    expect(repo).not.toBe("")
    const expected = perRepo.get(repo) ?? 0

    const first = await readPage({ repo, pageSize: 5 })
    expect(first.total).toBe(expected)
    expect(first.totalPages).toBe(Math.ceil(expected / 5))
    expect(first.pageSize).toBe(5)
    expect(first.items).toHaveLength(5)

    const second = await readPage({ repo, pageSize: 5, page: 2 })
    expect(second.items).toHaveLength(5)
    expect(
      second.items.some((row) => first.items.some((item) => item.id === row.id)),
    ).toBe(false)

    const clamped = await readPage({ repo, pageSize: 500 })
    expect(clamped.pageSize).toBe(100)

    const reread = await allRows()
    expect(reread.map((row) => row.id)).toEqual(rows.map((row) => row.id))
  })

  it("reports the backlog over the filtered set", async () => {
    const stale = await allRows({ review: "stale", drafts: 1 })
    const page = await readPage({ review: "stale", drafts: 1, pageSize: 5 })

    expect(page.summary).toEqual({
      total: stale.length,
      needsReview: stale.length,
      stale: stale.length,
      running: 0,
    })
    expect(page.total).toBe(stale.length)
    expect(page.items.length).toBe(Math.min(5, stale.length))
  })

  it("serves filter options for the whole inbox rather than the page", async () => {
    const rows = await allRows()
    const page = await readPage({
      repo: rows[0].repository.fullName,
      pageSize: 1,
    })

    expect(page.items).toHaveLength(1)
    expect(page.filterOptions.repositories.length).toBeGreaterThan(1)
    expect(
      page.filterOptions.reviews.map((option) => option.value).sort(),
    ).toEqual(["failed", "never", "queued", "reviewed", "running", "stale"])
    expect(
      page.filterOptions.checks.map((option) => option.value).sort(),
    ).toEqual(["failing", "none", "passing", "pending"])
    for (const option of page.filterOptions.repositories) {
      expect(option.hint).toBeTruthy()
    }
  })

  it("answers the empty scenario with no rows and a whole-set filter bar", async () => {
    const response = await fetch("/api/pull-requests?pageSize=100", {
      headers: { "x-mock-scenario": "empty" },
    })
    const body = (await response.json()) as PullRequestListResponse

    expect(response.status).toBe(200)
    expect(body.items).toEqual([])
    expect(body.total).toBe(0)
    expect(body.totalPages).toBe(0)
    expect(body.summary).toEqual({
      total: 0,
      needsReview: 0,
      stale: 0,
      running: 0,
    })
    expect(body.filterOptions.repositories.length).toBeGreaterThan(0)
  })

  it("answers the error scenario with the shared error envelope", async () => {
    const response = await fetch("/api/pull-requests", {
      headers: { "x-mock-scenario": "error" },
    })
    const body = (await response.json()) as ApiErrorBody

    expect(response.status).toBe(500)
    expect(body.error.code).toBe("pull_requests_unavailable")
  })

  it("drops a parked repository's pull requests from a later read", async () => {
    const before = await allRows()
    const fullName = before[0].repository.fullName
    const inRepo = before.filter((row) => row.repository.fullName === fullName)
    expect(inRepo.length).toBeGreaterThan(0)
    const target = (await listRepositories()).find(
      (item) => item.fullName === fullName,
    )
    expect(target?.enabled).toBe(true)

    try {
      const parked = await patchRepository(target?.id ?? "", { enabled: false })
      expect(parked.status).toBe(200)

      const after = await allRows()
      expect(after).toHaveLength(before.length - inRepo.length)
      expect(after.some((row) => row.repository.fullName === fullName)).toBe(false)

      const options = (await readPage()).filterOptions.repositories.map(
        (option) => option.value,
      )
      expect(options).not.toContain(fullName)
    } finally {
      await patchRepository(target?.id ?? "", { enabled: true })
    }
  })
})
