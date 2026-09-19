import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { api } from "@/api/client"
import type { DashboardData, UserRef } from "@/api/contract"
import App from "./App"

vi.mock("@/api/client", () => ({
  api: {
    getMe: vi.fn(),
    getDashboard: vi.fn(),
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
    listRepositoryPullRequests: vi.fn(),
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
  isMockModeEnabled: () => false,
  getMockScenario: () => "default",
  setMockScenario: vi.fn(),
}))

const adminUser: UserRef = {
  id: "usr_noueii",
  handle: "noueii",
  name: "Noah Yu",
  isAdmin: true,
}

const emptyDashboard: DashboardData = {
  scope: "All repositories",
  summary: {
    scope: "All repositories",
    totalSessions: 0,
    running: 0,
    failed: 0,
    spendUsd: 0,
    tokens: 0,
  },
  running: [],
  recent: [],
  generatedAt: "2026-01-01T00:00:00.000Z",
}

beforeEach(() => {
  vi.mocked(api.getMe).mockResolvedValue(adminUser)
  vi.mocked(api.getDashboard).mockResolvedValue(emptyDashboard)
  vi.mocked(api.listRepositories).mockResolvedValue({ items: [] })
  vi.mocked(api.listPresets).mockResolvedValue({
    defaultPresetId: "default",
    presets: [{ id: "default", name: "Default", description: "Balanced." }],
  })
})

afterEach(cleanup)

describe("App navigation and focus intent", () => {
  it("does not expose a New Review nav item", async () => {
    render(<App />)
    await screen.findByLabelText("Review focus")

    expect(screen.queryByRole("button", { name: "New Review" })).toBeNull()
  })

  it("hides the Admin group from non-admins", async () => {
    vi.mocked(api.getMe).mockResolvedValue({ ...adminUser, isAdmin: false })
    render(<App />)
    await screen.findByLabelText("Review focus")

    const sidebar = within(screen.getByRole("complementary"))
    expect(sidebar.getByText("Workspace")).toBeDefined()
    expect(sidebar.getByText("Repositories")).toBeDefined()
    expect(sidebar.queryByText("Admin")).toBeNull()
    expect(sidebar.queryByText("Review templates")).toBeNull()
  })

  it("shows the Admin group to admins", async () => {
    render(<App />)
    await screen.findByLabelText("Review focus")

    const sidebar = within(screen.getByRole("complementary"))
    expect(sidebar.getByText("Admin")).toBeDefined()
    expect(sidebar.getByText("Review templates")).toBeDefined()
  })

  it("falls back to a guest menu when /api/me fails", async () => {
    vi.mocked(api.getMe).mockRejectedValue(new Error("unauthorized"))
    render(<App />)
    await screen.findByLabelText("Review focus")

    expect(within(screen.getByRole("complementary")).queryByText("Review templates")).toBeNull()
    expect(screen.getByRole("button", { name: "Account menu" })).toBeDefined()
  })

  it("focuses the dashboard composer when New review is triggered", async () => {
    render(<App />)
    const composer = await screen.findByLabelText("Review focus")
    expect(document.activeElement).not.toBe(composer)

    fireEvent.click(screen.getByRole("button", { name: "New review" }))

    await waitFor(
      () => {
        expect(document.activeElement).toBe(composer)
      },
      { timeout: 2000 },
    )
  })
})
