import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { api } from "@/api/client"
import type * as client from "@/api/client"
import type { RepositorySummary } from "@/api/contract"
import { RepositoriesScreen } from "./RepositoriesScreen"

vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof client>()
  return {
    githubAppInstallUrl: actual.githubAppInstallUrl,
    api: {
      listRepositories: vi.fn(),
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
  }
})

const repository: RepositorySummary = {
  id: "repo_1",
  fullName: "acme/api-gateway",
  private: true,
  defaultBranch: "main",
  openPrCount: 3,
  lastActivityAt: "2026-01-01T00:00:00.000Z",
  connected: true,
  enabled: true,
}

afterEach(cleanup)

describe("RepositoriesScreen", () => {
  it("opens the repository matching the clicked card", async () => {
    vi.mocked(api.listRepositories).mockResolvedValue({ items: [repository] })
    const onOpenRepository = vi.fn()
    render(<RepositoriesScreen onOpenRepository={onOpenRepository} />)

    const card = await screen.findByRole("button", {
      name: "Open repository acme/api-gateway",
    })
    fireEvent.click(card)

    expect(onOpenRepository).toHaveBeenCalledTimes(1)
    expect(onOpenRepository).toHaveBeenCalledWith("acme/api-gateway")
  })

  it("keeps the existing card content", async () => {
    vi.mocked(api.listRepositories).mockResolvedValue({ items: [repository] })
    render(<RepositoriesScreen onOpenRepository={vi.fn()} />)

    await screen.findByRole("button", {
      name: "Open repository acme/api-gateway",
    })
    expect(screen.getByText("acme/api-gateway")).toBeDefined()
    expect(screen.getByText("Private")).toBeDefined()
    expect(screen.getByText("main")).toBeDefined()
    expect(screen.getByText("3 open PRs")).toBeDefined()
    expect(screen.getByText("Connected")).toBeDefined()
  })

  it("sends Connect repository into the GitHub App install flow", async () => {
    vi.mocked(api.listRepositories).mockResolvedValue({ items: [] })
    render(<RepositoriesScreen onOpenRepository={vi.fn()} />)

    const connect = await screen.findByRole("link", {
      name: "Connect repository",
    })

    // The install route redirects on to GitHub; the login route would just
    // re-authenticate the visitor and drop them back on the dashboard.
    expect(connect.getAttribute("href")).toBe("/api/github/install")
  })

  it("keeps a parked repository listed and enables it on request", async () => {
    vi.mocked(api.listRepositories).mockResolvedValue({
      items: [{ ...repository, enabled: false }],
    })
    vi.mocked(api.updateRepository).mockResolvedValue({
      ...repository,
      enabled: true,
    })
    render(<RepositoriesScreen onOpenRepository={vi.fn()} />)

    // The row survives parking, marked as disabled, with its history intact
    expect(await screen.findByText("Disabled")).toBeDefined()
    expect(screen.getByText("acme/api-gateway")).toBeDefined()
    expect(screen.getByText("3 open PRs")).toBeDefined()

    // Enabling it reopens the repository without asking anything first
    fireEvent.click(screen.getByRole("button", { name: "Enable" }))

    await waitFor(() =>
      expect(api.updateRepository).toHaveBeenCalledWith("repo_1", {
        enabled: true,
      }),
    )
  })

  it("explains what parking does before disabling a repository", async () => {
    vi.mocked(api.listRepositories).mockResolvedValue({ items: [repository] })
    vi.mocked(api.updateRepository).mockResolvedValue({
      ...repository,
      enabled: false,
    })
    render(<RepositoriesScreen onOpenRepository={vi.fn()} />)

    fireEvent.click(await screen.findByRole("button", { name: "Disable" }))

    // Nothing is sent until the consequence is spelled out and confirmed
    expect(await screen.findByText("Disable acme/api-gateway?")).toBeDefined()
    expect(screen.getByText(/pre-flight refuses its pull requests/)).toBeDefined()
    expect(api.updateRepository).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole("button", { name: "Disable repository" }))

    await waitFor(() =>
      expect(api.updateRepository).toHaveBeenCalledWith("repo_1", {
        enabled: false,
      }),
    )
  })
})
