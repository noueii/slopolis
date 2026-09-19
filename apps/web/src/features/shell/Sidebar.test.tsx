import { cleanup, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { Sidebar } from "./Sidebar"

afterEach(cleanup)

describe("Sidebar role gating", () => {
  it("hides the Admin group from non-admins", () => {
    render(<Sidebar active="dashboard" onNavigate={vi.fn()} isAdmin={false} />)

    expect(screen.getByText("Workspace")).toBeDefined()
    expect(screen.getByText("Repositories")).toBeDefined()
    expect(screen.queryByText("Admin")).toBeNull()
    expect(screen.queryByText("Providers & Models")).toBeNull()
    expect(screen.queryByText("Review templates")).toBeNull()
    expect(screen.queryByText("Settings")).toBeNull()
  })

  it("shows the Admin group to admins", () => {
    render(<Sidebar active="dashboard" onNavigate={vi.fn()} isAdmin />)

    expect(screen.getByText("Workspace")).toBeDefined()
    expect(screen.getByText("Admin")).toBeDefined()
    expect(screen.getByText("Providers & Models")).toBeDefined()
    expect(screen.getByText("Review templates")).toBeDefined()
    expect(screen.getByText("Settings")).toBeDefined()
  })

  it("keeps the active-item indicator wired to aria-current", () => {
    render(<Sidebar active="sessions" onNavigate={vi.fn()} isAdmin={false} />)

    const active = screen.getByRole("button", { name: "Sessions" })
    expect(active.getAttribute("aria-current")).toBe("page")
    expect(
      screen.getByRole("button", { name: "Dashboard" }).getAttribute("aria-current"),
    ).toBeNull()
  })

  it("navigates to the requested item", () => {
    const onNavigate = vi.fn()
    render(<Sidebar active="dashboard" onNavigate={onNavigate} isAdmin={false} />)

    screen.getByRole("button", { name: "Repositories" }).click()
    expect(onNavigate).toHaveBeenCalledWith("repositories")
  })
})
