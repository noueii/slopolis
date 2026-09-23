/**
 * The inbox screen's observable behaviour: what a row says about its review,
 * what selecting a row does to the dock, what a filter change requests, and
 * which copy the empty and error states render.
 *
 * The API is mocked at the client boundary (as `App.test.tsx` does) because
 * this suite is about the screen, not about MSW's routing.
 */

import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { api, ApiError } from "@/api/client"
import type {
  PullRequestListItem,
  PullRequestListResponse,
  PullRequestReview,
  RepositoryRef,
  UserRef,
} from "@/api/contract"
import { TooltipProvider } from "@/components/ui/tooltip"
import {
  PullRequestsScreen,
  type PullRequestsScreenProps,
} from "./PullRequestsScreen"

vi.mock("@/api/client", () => ({
  api: {
    listPullRequests: vi.fn(),
    listRepositories: vi.fn(),
    listPresets: vi.fn(),
    preflightReview: vi.fn(),
    createReviewSession: vi.fn(),
  },
  ApiError: class ApiError extends Error {
    readonly status: number
    readonly code: string

    constructor(status: number, code: string, message: string) {
      super(message)
      this.name = "ApiError"
      this.status = status
      this.code = code
    }
  },
}))

const repository: RepositoryRef = {
  id: "repo_1",
  fullName: "acme/api-gateway",
  private: true,
}

const author: UserRef = {
  id: "usr_1",
  handle: "noueii",
  name: "Noah Yu",
  isAdmin: false,
}

function review(overrides: Partial<PullRequestReview> = {}): PullRequestReview {
  return {
    state: "reviewed",
    sessionId: "sess_head",
    reviewedSha: "sha_head",
    commitsSinceReview: 0,
    findingsCount: 3,
    progress: null,
    step: null,
    reviewedAt: "2026-09-20T09:30:00.000Z",
    ...overrides,
  }
}

const current: PullRequestListItem = {
  id: "pr_142",
  repository,
  number: 142,
  title: "Guard token refresh skew",
  url: "https://github.com/acme/api-gateway/pull/142",
  author,
  headBranch: "guard-token-refresh",
  headSha: "sha_head",
  updatedAt: "2026-09-21T08:00:00.000Z",
  draft: false,
  comments: 2,
  changedFiles: 6,
  additions: 120,
  deletions: 40,
  checks: { state: "passing", total: 4, passing: 4 },
  review: review(),
}

const behind: PullRequestListItem = {
  ...current,
  id: "pr_143",
  number: 143,
  title: "Rotate webhook secrets",
  url: "https://github.com/acme/api-gateway/pull/143",
  updatedAt: "2026-09-20T18:00:00.000Z",
  checks: { state: "failing", total: 3, passing: 1 },
  review: review({
    sessionId: "sess_behind",
    reviewedSha: "sha_older",
    commitsSinceReview: 2,
  }),
}

function listResponse(
  items: PullRequestListItem[],
  overrides: Partial<PullRequestListResponse> = {},
): PullRequestListResponse {
  return {
    items,
    page: 1,
    pageSize: 25,
    total: items.length,
    totalPages: 1,
    summary: {
      total: items.length,
      needsReview: items.length,
      stale: items.filter(
        (item) =>
          item.review.state === "reviewed" &&
          item.review.commitsSinceReview !== 0,
      ).length,
      running: 0,
    },
    filterOptions: {
      repositories: [
        {
          value: "acme/api-gateway",
          label: "acme/api-gateway",
          hint: String(items.length),
        },
      ],
      reviews: [
        { value: "never", label: "Not reviewed" },
        { value: "queued", label: "Queued" },
        { value: "running", label: "Running" },
        { value: "reviewed", label: "Reviewed" },
        { value: "stale", label: "Behind the head", hint: "1" },
        { value: "failed", label: "Failed" },
      ],
      checks: [
        { value: "passing", label: "Passing" },
        { value: "failing", label: "Failing" },
        { value: "pending", label: "Pending" },
        { value: "none", label: "No checks" },
      ],
    },
    generatedAt: "2026-09-21T09:00:00.000Z",
    ...overrides,
  }
}

const connectedRepository = {
  id: repository.id,
  fullName: repository.fullName,
  private: repository.private,
  defaultBranch: "main",
  openPrCount: 2,
  lastActivityAt: "2026-09-21T08:00:00.000Z",
  connected: true,
  enabled: true,
}

beforeEach(() => {
  vi.mocked(api.listPullRequests).mockResolvedValue(listResponse([current, behind]))
  vi.mocked(api.listRepositories).mockResolvedValue({
    items: [connectedRepository],
  })
  vi.mocked(api.listPresets).mockResolvedValue({
    defaultPresetId: "default",
    presets: [{ id: "default", name: "Default", description: "Balanced." }],
  })
})

afterEach(cleanup)

function element(props: Partial<PullRequestsScreenProps> = {}) {
  return (
    <TooltipProvider>
      <PullRequestsScreen
        scenario="default"
        onOpenSession={() => undefined}
        {...props}
      />
    </TooltipProvider>
  )
}

function renderScreen(props: Partial<PullRequestsScreenProps> = {}) {
  return render(element(props))
}

function lastParams(): unknown {
  const calls = vi.mocked(api.listPullRequests).mock.calls
  return calls.length > 0 ? calls[calls.length - 1][0] : undefined
}

describe("review state", () => {
  it("shows how far behind a stale review is instead of calling it reviewed", async () => {
    renderScreen()
    await screen.findByText(current.title)

    // Only the current review is a plain "Reviewed"; the behind row says so.
    expect(screen.getAllByText("Reviewed")).toHaveLength(1)
    expect(screen.getByText("2 commits behind")).toBeDefined()
  })

  it("links the row's title to GitHub in a new tab", async () => {
    renderScreen()

    const link = await screen.findByRole("link", { name: current.title })
    expect(link.getAttribute("href")).toBe(current.url)
    expect(link.getAttribute("target")).toBe("_blank")
  })
})

describe("selection", () => {
  it("raises the dock when a row is clicked, and unselects it from its chip", async () => {
    renderScreen()
    const rows = await screen.findAllByRole("listitem")

    fireEvent.click(rows[1])

    const chip = await screen.findByLabelText("Remove pull request #143")
    fireEvent.click(chip)

    await waitFor(() =>
      expect(screen.queryByLabelText("Remove pull request #143")).toBeNull(),
    )
  })

  it("extends the selection across a shift-clicked range", async () => {
    renderScreen()
    const rows = await screen.findAllByRole("listitem")

    fireEvent.click(rows[0])
    fireEvent.click(rows[1], { shiftKey: true })

    expect(await screen.findByLabelText("Remove pull request #142")).toBeDefined()
    expect(screen.getByLabelText("Remove pull request #143")).toBeDefined()
  })
})

describe("filters", () => {
  it("requests the baseline params, then re-requests when a filter changes", async () => {
    renderScreen()
    await screen.findAllByRole("listitem")

    expect(vi.mocked(api.listPullRequests).mock.calls[0][0]).toEqual({
      page: 1,
      pageSize: 25,
      sort: "updated_desc",
    })

    fireEvent.click(screen.getByLabelText("Include draft pull requests"))

    await waitFor(() =>
      expect(lastParams()).toEqual({
        page: 1,
        pageSize: 25,
        sort: "updated_desc",
        includeDrafts: true,
      }),
    )
  })

  it("debounces the search text into the query on the first page", async () => {
    renderScreen()
    await screen.findAllByRole("listitem")

    fireEvent.change(screen.getByLabelText("Search pull requests"), {
      target: { value: "token" },
    })

    await waitFor(() =>
      expect(lastParams()).toMatchObject({ q: "token", page: 1 }),
    )
  })
})

describe("shell integration", () => {
  it("focuses the search field when the shell bumps focusSearchNonce", async () => {
    const view = renderScreen({ scenario: "default", focusSearchNonce: 0 })
    await screen.findAllByRole("listitem")
    const search = screen.getByLabelText("Search pull requests")
    expect(document.activeElement).not.toBe(search)

    view.rerender(element({ scenario: "default", focusSearchNonce: 1 }))

    await waitFor(() => expect(document.activeElement).toBe(search))
  })

  it("re-requests on a scenario change, but not on the first render", async () => {
    const view = renderScreen({ scenario: "default" })
    await screen.findAllByRole("listitem")
    expect(api.listPullRequests).toHaveBeenCalledTimes(1)

    view.rerender(element({ scenario: "empty" }))

    await waitFor(() => expect(api.listPullRequests).toHaveBeenCalledTimes(2))
  })
})

describe("states", () => {
  it("reports the failure with a retry while the filter bar stays usable", async () => {
    vi.mocked(api.listPullRequests).mockRejectedValue(
      new ApiError(503, "unavailable", "The inbox is unavailable."),
    )
    renderScreen()

    expect(await screen.findByText("Could not load pull requests")).toBeDefined()
    expect(screen.getByText("The inbox is unavailable.")).toBeDefined()
    expect(screen.getByLabelText("Search pull requests")).toBeDefined()

    fireEvent.click(screen.getByRole("button", { name: "Try again" }))
    await waitFor(() => expect(api.listPullRequests).toHaveBeenCalledTimes(2))
  })

  it("points at the Repositories screen when nothing is connected", async () => {
    vi.mocked(api.listPullRequests).mockResolvedValue(listResponse([]))
    vi.mocked(api.listRepositories).mockResolvedValue({ items: [] })
    renderScreen()

    expect(await screen.findByText("No repositories connected")).toBeDefined()
    expect(
      screen.getByRole("button", { name: "Go to Repositories" }),
    ).toBeDefined()
  })

  it("says nothing is open when connected repositories have no open pull requests", async () => {
    vi.mocked(api.listPullRequests).mockResolvedValue(listResponse([]))
    renderScreen()

    expect(await screen.findByText("Nothing open")).toBeDefined()
    expect(screen.queryByText("No repositories connected")).toBeNull()
    expect(screen.queryByRole("button", { name: "Clear filters" })).toBeNull()
  })

  it("offers to clear the filters when they match nothing", async () => {
    vi.mocked(api.listPullRequests).mockResolvedValue(listResponse([]))
    renderScreen()
    await screen.findByText("Nothing open")

    fireEvent.change(screen.getByLabelText("Search pull requests"), {
      target: { value: "zzz" },
    })

    expect(
      await screen.findByText("No pull requests match your filters"),
    ).toBeDefined()

    fireEvent.click(screen.getByRole("button", { name: "Clear filters" }))

    await waitFor(() =>
      expect(
        (screen.getByLabelText("Search pull requests") as HTMLInputElement).value,
      ).toBe(""),
    )
  })
})
