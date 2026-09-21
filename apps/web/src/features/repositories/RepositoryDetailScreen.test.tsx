import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { api } from "@/api/client"
import type { RepositorySummary, ReviewSession } from "@/api/contract"
import { RepositoryDetailScreen } from "./RepositoryDetailScreen"

vi.mock("@/api/client", () => ({
  api: {
    listRepositories: vi.fn(),
    listSessions: vi.fn(),
    updateRepository: vi.fn(),
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
}))

const repository: RepositorySummary = {
  id: "repo_1",
  fullName: "acme/api-gateway",
  private: true,
  defaultBranch: "main",
  openPrCount: 3,
  lastActivityAt: "2026-01-01T00:00:00.000Z",
  requiredAccess: "default",
  connected: true,
  enabled: true,
}

const session: ReviewSession = {
  id: "ses_1",
  title: "Guard token refresh skew",
  name: "acme/api-gateway#142",
  status: "done",
  model: "gpt-4o",
  provider: "OpenAI",
  triggeredBy: {
    id: "usr_1",
    handle: "noueii",
    name: "Noah Yu",
    isAdmin: true,
  },
  createdAt: "2026-01-01T00:00:00.000Z",
  targets: [],
  targetCount: 0,
  tokens: 1200,
  costUsd: 0.02,
  findingsCount: 2,
}

afterEach(cleanup)

describe("RepositoryDetailScreen", () => {
  it("lists the sessions returned for the repository and opens one on click", async () => {
    vi.mocked(api.listRepositories).mockResolvedValue({ items: [repository] })
    vi.mocked(api.listSessions).mockResolvedValue({
      items: [session],
      page: 1,
      pageSize: 50,
      total: 1,
      totalPages: 1,
    })
    const onOpenSession = vi.fn()

    render(
      <RepositoryDetailScreen
        fullName="acme/api-gateway"
        onBack={vi.fn()}
        onOpenSession={onOpenSession}
      />,
    )

    const row = await screen.findByLabelText("Open session acme/api-gateway#142")
    fireEvent.click(row)

    expect(onOpenSession).toHaveBeenCalledWith("ses_1")
    expect(api.listSessions).toHaveBeenCalledWith({
      repo: "acme/api-gateway",
      pageSize: 50,
      sort: "created_desc",
    })
    expect(screen.getByText("slopolis / Repositories / acme/api-gateway")).toBeDefined()
  })

  it("shows the repository-scoped empty state when there are no sessions", async () => {
    vi.mocked(api.listRepositories).mockResolvedValue({ items: [repository] })
    vi.mocked(api.listSessions).mockResolvedValue({
      items: [],
      page: 1,
      pageSize: 50,
      total: 0,
      totalPages: 0,
    })

    render(
      <RepositoryDetailScreen
        fullName="acme/api-gateway"
        onBack={vi.fn()}
        onOpenSession={vi.fn()}
      />,
    )

    expect(
      await screen.findByText("No review sessions for acme/api-gateway yet"),
    ).toBeDefined()
  })

  it("degrades gracefully when the repository is not in the workspace list", async () => {
    vi.mocked(api.listRepositories).mockResolvedValue({ items: [] })
    vi.mocked(api.listSessions).mockResolvedValue({
      items: [],
      page: 1,
      pageSize: 50,
      total: 0,
      totalPages: 0,
    })

    render(
      <RepositoryDetailScreen
        fullName="ghost/repo"
        onBack={vi.fn()}
        onOpenSession={vi.fn()}
      />,
    )

    expect(await screen.findByText("Repository details unavailable")).toBeDefined()
    expect(
      screen.getByText("No review sessions for ghost/repo yet"),
    ).toBeDefined()
  })

  it("shows a parked repository with its history and enables it on request", async () => {
    vi.mocked(api.listRepositories).mockResolvedValue({
      items: [{ ...repository, enabled: false }],
    })
    vi.mocked(api.listSessions).mockResolvedValue({
      items: [session],
      page: 1,
      pageSize: 50,
      total: 1,
      totalPages: 1,
    })
    vi.mocked(api.updateRepository).mockResolvedValue({
      ...repository,
      enabled: true,
    })

    render(
      <RepositoryDetailScreen
        fullName="acme/api-gateway"
        onBack={vi.fn()}
        onOpenSession={vi.fn()}
      />,
    )

    // The parked state is named, with its consequence, and the history stays
    expect(await screen.findByText("Disabled")).toBeDefined()
    expect(
      screen.getByText(/pre-flight refuses its pull requests/),
    ).toBeDefined()
    expect(
      screen.getByLabelText("Open session acme/api-gateway#142"),
    ).toBeDefined()

    fireEvent.click(screen.getByRole("button", { name: "Enable" }))

    await waitFor(() =>
      expect(api.updateRepository).toHaveBeenCalledWith("repo_1", {
        enabled: true,
      }),
    )
  })

  it("shows the repository's review access rule and saves a change to it", async () => {
    vi.mocked(api.listRepositories).mockResolvedValue({
      items: [{ ...repository, requiredAccess: "read" }],
    })
    vi.mocked(api.listSessions).mockResolvedValue({
      items: [],
      page: 1,
      pageSize: 50,
      total: 0,
      totalPages: 0,
    })
    vi.mocked(api.updateRepository).mockResolvedValue({
      ...repository,
      requiredAccess: "write",
    })

    render(
      <RepositoryDetailScreen
        fullName="acme/api-gateway"
        onBack={vi.fn()}
        onOpenSession={vi.fn()}
      />,
    )

    // The stored rule is the one selected, and the refusal it produces is named
    const read = await screen.findByRole("radio", { name: /^Read / })
    expect((read as HTMLInputElement).checked).toBe(true)
    expect(
      screen.getByText(/^read access is required on acme\/api-gateway$/, {
        selector: "code",
      }),
    ).toBeDefined()

    // Choosing the tightening rule previews the refusal it would produce
    fireEvent.click(screen.getByRole("radio", { name: /^Write / }))
    expect(
      screen.getByText(/^write access is required on acme\/api-gateway$/, {
        selector: "code",
      }),
    ).toBeDefined()

    fireEvent.click(screen.getByRole("button", { name: "Save" }))

    // Only the rule travels: the parked state is not resent by this control
    await waitFor(() =>
      expect(api.updateRepository).toHaveBeenCalledWith("repo_1", {
        requiredAccess: "write",
      }),
    )
  })
})
