import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import {
  api,
  ApiError,
  beginSignIn,
  signInAttempted,
} from "@/api/client"
import type {
  MeResponse,
  PullRequestListResponse,
  ReviewSession,
  WorkspaceRef,
} from "@/api/contract"
import App from "./App"

// The session detail subscribes to the SSE stream on mount; this suite is about
// which screen the URL selects, so the transport stays out of it.
vi.mock("@/api/events", () => ({
  TERMINAL_SESSION_STATUSES: new Set(["done", "failed", "cancelled"]),
  subscribeToSession: () => () => undefined,
}))

vi.mock("@/api/client", () => ({
  githubAppInstallUrl: "/api/github/install",
  api: {
    getMe: vi.fn(),
    listWorkspaces: vi.fn(),
    createWorkspace: vi.fn(),
    listPullRequests: vi.fn(),
    listRepositories: vi.fn(),
    listPresets: vi.fn(),
    listModels: vi.fn(),
    listSessions: vi.fn(),
    getFilterOptions: vi.fn(),
    getSessionStats: vi.fn(),
    getSession: vi.fn(),
    listTemplates: vi.fn(),
    getTemplate: vi.fn(),
    createTemplate: vi.fn(),
    updateTemplate: vi.fn(),
    preflightReview: vi.fn(),
    createReviewSession: vi.fn(),
    getRunTree: vi.fn(),
    getRunEvents: vi.fn(),
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
  isMockModeEnabled: () => false,
  getMockScenario: () => "default",
  setMockScenario: vi.fn(),
  beginSignIn: vi.fn(),
  signInAttempted: vi.fn(),
  clearSignInAttempt: vi.fn(),
}))

const workspace: WorkspaceRef = {
  id: "ws_acme_labs",
  name: "Acme Labs",
  slug: "acme-labs",
}

const adminUser: MeResponse = {
  id: "usr_noueii",
  handle: "noueii",
  name: "Noah Yu",
  isAdmin: true,
  workspace,
}

const emptyInbox: PullRequestListResponse = {
  items: [],
  page: 1,
  pageSize: 25,
  total: 0,
  totalPages: 0,
  summary: { total: 0, needsReview: 0, stale: 0, running: 0 },
  filterOptions: { repositories: [], reviews: [], checks: [] },
  generatedAt: "2026-01-01T00:00:00.000Z",
}

const session: ReviewSession = {
  id: "sess_7f3a",
  title: "Guard token refresh skew",
  name: "acme/api-gateway#142",
  status: "done",
  model: "claude-sonnet-4",
  provider: "Anthropic",
  triggeredBy: { id: "usr_noueii", handle: "noueii", name: "Noah Yu", isAdmin: true },
  createdAt: "2026-01-01T00:00:00.000Z",
  targets: [],
  targetCount: 0,
  tokens: 0,
  costUsd: 0,
  findingsCount: 0,
}

beforeEach(() => {
  vi.mocked(signInAttempted).mockReturnValue(false)
  vi.mocked(beginSignIn).mockResolvedValue({ started: true })
  vi.mocked(api.getMe).mockResolvedValue(adminUser)
  vi.mocked(api.listPullRequests).mockResolvedValue(emptyInbox)
  vi.mocked(api.listRepositories).mockResolvedValue({ items: [] })
  vi.mocked(api.listPresets).mockResolvedValue({
    defaultPresetId: "default",
    presets: [{ id: "default", name: "Default", description: "Balanced." }],
  })
  // The session detail mounts the run-tree panel, so a session view needs a tree.
  vi.mocked(api.getRunTree).mockResolvedValue({ runs: [] })
  vi.mocked(api.getRunEvents).mockResolvedValue({ items: [], nextSeq: null })
})

afterEach(() => {
  cleanup()
  // `navigate` pushes real history entries; a test must not inherit them.
  window.history.replaceState(null, "", "/")
})

describe("App navigation and focus intent", () => {
  it("does not expose a New Review nav item", async () => {
    render(<App />)
    await screen.findByLabelText("Search pull requests")

    expect(screen.queryByRole("button", { name: "New Review" })).toBeNull()
  })

  it("hides the Admin group from non-admins", async () => {
    vi.mocked(api.getMe).mockResolvedValue({ ...adminUser, isAdmin: false })
    render(<App />)
    await screen.findByLabelText("Search pull requests")

    const sidebar = within(screen.getByRole("complementary"))
    expect(sidebar.getByText("Workspace")).toBeDefined()
    expect(sidebar.getByText("Repositories")).toBeDefined()
    expect(sidebar.queryByText("Admin")).toBeNull()
    expect(sidebar.queryByText("Review templates")).toBeNull()
  })

  it("shows the Admin group to admins", async () => {
    render(<App />)
    await screen.findByLabelText("Search pull requests")

    const sidebar = within(screen.getByRole("complementary"))
    expect(sidebar.getByText("Admin")).toBeDefined()
    expect(sidebar.getByText("Review templates")).toBeDefined()
  })

  it("focuses the inbox search when New review is triggered", async () => {
    render(<App />)
    const search = await screen.findByLabelText("Search pull requests")
    expect(document.activeElement).not.toBe(search)

    fireEvent.click(screen.getByRole("button", { name: "New review" }))

    await waitFor(
      () => {
        expect(document.activeElement).toBe(search)
      },
      { timeout: 2000 },
    )
  })
})

describe("Sign-in gate", () => {
  beforeEach(() => {
    vi.mocked(api.getMe).mockRejectedValue(
      new ApiError(401, "unauthorized", "Sign in with GitHub to continue."),
    )
  })

  it("sends an account-less visitor to GitHub sign-in", async () => {
    render(<App />)

    await waitFor(() => expect(beginSignIn).toHaveBeenCalledTimes(1))
    expect(screen.queryByRole("complementary")).toBeNull()
    expect(screen.queryByLabelText("Search pull requests")).toBeNull()
  })

  it("leaves a tab that already came back unsigned on the gate", async () => {
    vi.mocked(signInAttempted).mockReturnValue(true)
    render(<App />)

    expect(await screen.findByText(/did not sign this browser in/i)).toBeDefined()
    expect(beginSignIn).not.toHaveBeenCalled()
  })

  it("reports why the flow could not start, and retries on demand", async () => {
    vi.mocked(beginSignIn).mockResolvedValue({
      started: false,
      message: "GitHub OAuth credentials are not configured.",
    })
    render(<App />)

    expect(
      await screen.findByText("GitHub OAuth credentials are not configured."),
    ).toBeDefined()

    fireEvent.click(screen.getByRole("button", { name: "Try again" }))
    await waitFor(() => expect(beginSignIn).toHaveBeenCalledTimes(2))
  })
})

describe("Workspace onboarding gate", () => {
  const workspaceLess: MeResponse = { ...adminUser, workspace: null }

  it("renders onboarding instead of the shell for an account with no workspace", async () => {
    vi.mocked(api.getMe).mockResolvedValue(workspaceLess)
    render(<App />)

    expect(await screen.findByLabelText("Workspace name")).toBeDefined()
    expect(screen.getByText("@noueii")).toBeDefined()
    expect(screen.getByRole("button", { name: "Check again" })).toBeDefined()
    expect(screen.queryByLabelText("Search pull requests")).toBeNull()
    expect(screen.queryByRole("complementary")).toBeNull()
  })

  it("enters the shell after creating a workspace", async () => {
    vi.mocked(api.getMe)
      .mockResolvedValueOnce(workspaceLess)
      .mockResolvedValue(adminUser)
    vi.mocked(api.createWorkspace).mockResolvedValue(workspace)
    render(<App />)

    const input = (await screen.findByLabelText("Workspace name")) as HTMLInputElement
    expect(input.value).toBe("noueii's workspace")
    fireEvent.change(input, { target: { value: "Acme Labs" } })
    fireEvent.click(screen.getByRole("button", { name: "Create workspace" }))

    await screen.findByLabelText("Search pull requests")
    expect(api.createWorkspace).toHaveBeenCalledWith("Acme Labs")
    expect(
      within(screen.getByRole("complementary")).getByText("Acme Labs"),
    ).toBeDefined()
  })

  it("rejects a blank workspace name without calling the API", async () => {
    vi.mocked(api.getMe).mockResolvedValue(workspaceLess)
    render(<App />)

    const input = await screen.findByLabelText("Workspace name")
    fireEvent.change(input, { target: { value: "   " } })
    fireEvent.click(screen.getByRole("button", { name: "Create workspace" }))

    expect(await screen.findByRole("alert")).toBeDefined()
    expect(api.createWorkspace).not.toHaveBeenCalled()
  })

  it("surfaces the already_in_workspace conflict", async () => {
    vi.mocked(api.getMe).mockResolvedValue(workspaceLess)
    vi.mocked(api.createWorkspace).mockRejectedValue(
      new ApiError(
        409,
        "already_in_workspace",
        "This account already belongs to a workspace.",
      ),
    )
    render(<App />)

    fireEvent.click(await screen.findByRole("button", { name: "Create workspace" }))

    const alert = await screen.findByRole("alert")
    expect(alert.textContent).toContain("already belongs to a workspace")
  })

  it("re-reads /me from the invitation path", async () => {
    vi.mocked(api.getMe).mockResolvedValue(workspaceLess)
    render(<App />)

    fireEvent.click(await screen.findByRole("button", { name: "Check again" }))

    await waitFor(() => expect(api.getMe).toHaveBeenCalledTimes(2))
  })
})

describe("URL routing", () => {
  it("opens a session from its permalink", async () => {
    vi.mocked(api.getSession).mockResolvedValue(session)
    window.history.replaceState(null, "", "/sessions/sess_7f3a")

    render(<App />)

    expect(await screen.findByText("Guard token refresh skew")).toBeDefined()
    expect(api.getSession).toHaveBeenCalledWith("sess_7f3a")
    expect(
      within(screen.getByRole("complementary")).getByText("Sessions"),
    ).toBeDefined()
  })

  it("records a sidebar destination in the URL", async () => {
    render(<App />)
    await screen.findByLabelText("Search pull requests")

    fireEvent.click(
      within(screen.getByRole("complementary")).getByText("Repositories"),
    )

    await waitFor(() => expect(window.location.pathname).toBe("/repositories"))
  })

  it("falls back to the inbox for a path nothing owns", async () => {
    window.history.replaceState(null, "", "/nope/42")

    render(<App />)

    await screen.findByLabelText("Search pull requests")
    await waitFor(() => expect(window.location.pathname).toBe("/"))
  })
})
