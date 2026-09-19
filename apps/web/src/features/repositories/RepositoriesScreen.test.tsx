import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { api } from "@/api/client"
import type { RepositorySummary } from "@/api/contract"
import { RepositoriesScreen } from "./RepositoriesScreen"

vi.mock("@/api/client", () => ({
  api: {
    listRepositories: vi.fn(),
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
  connected: true,
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
})
