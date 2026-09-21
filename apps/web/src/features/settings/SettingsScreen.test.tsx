import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { ApiError, api } from "@/api/client"
import type * as client from "@/api/client"
import type { WorkspaceSettings } from "@/api/contract"
import { SettingsScreen } from "./SettingsScreen"

vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof client>()
  return {
    githubAppInstallUrl: actual.githubAppInstallUrl,
    api: {
      getWorkspaceSettings: vi.fn(),
      updateWorkspaceSettings: vi.fn(),
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

/** Two caps set, two left unset — the mixture the screen has to render. */
const CAPS: WorkspaceSettings = {
  maxConcurrentSessions: 4,
  maxSessionsPerUserPerDay: null,
  maxTargetsPerRepo: 2,
  maxTargetsPerInstallation: null,
}

afterEach(cleanup)

/** Render from a successful read; every case below starts from the caps above. */
function renderSettings(props: { onOpenRepositories?: () => void } = {}) {
  vi.mocked(api.getWorkspaceSettings).mockResolvedValue(CAPS)
  vi.mocked(api.updateWorkspaceSettings).mockResolvedValue(CAPS)
  render(<SettingsScreen {...props} />)
}

async function capField(label: string): Promise<HTMLInputElement> {
  return (await screen.findByLabelText(label)) as HTMLInputElement
}

describe("SettingsScreen", () => {
  it("renders the caps the API returns and marks the unset ones unlimited", async () => {
    renderSettings()

    expect((await capField("Concurrent sessions")).value).toBe("4")
    expect((await capField("Targets per repository")).value).toBe("2")

    const perUser = await capField("Sessions per user per day")
    expect(perUser.value).toBe("")
    expect(perUser.placeholder).toBe("Unlimited")
    const perInstallation = await capField("Targets per installation")
    expect(perInstallation.value).toBe("")
    expect(perInstallation.placeholder).toBe("Unlimited")

    expect(screen.getByText("Session limits")).toBeTruthy()
    expect(screen.getByText("Queue concurrency")).toBeTruthy()
    expect(screen.getByText("Access control")).toBeTruthy()
  })

  it("sends null when a cap is cleared back to unlimited", async () => {
    renderSettings()

    fireEvent.change(await capField("Concurrent sessions"), {
      target: { value: "" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }))

    await waitFor(() =>
      expect(api.updateWorkspaceSettings).toHaveBeenCalledWith({
        maxConcurrentSessions: null,
      }),
    )
  })

  it("sends only the caps the admin changed", async () => {
    renderSettings()

    fireEvent.change(await capField("Targets per repository"), {
      target: { value: "6" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }))

    await waitFor(() =>
      expect(api.updateWorkspaceSettings).toHaveBeenCalledTimes(1),
    )
    expect(api.updateWorkspaceSettings).toHaveBeenCalledWith({
      maxTargetsPerRepo: 6,
    })
  })

  it.each(["0", "-3", "two", "1.5"])(
    "refuses %s in the form without calling the API",
    async (value) => {
      renderSettings()

      fireEvent.change(await capField("Concurrent sessions"), {
        target: { value },
      })

      expect(screen.getByRole("alert").textContent).toContain("whole number")
      fireEvent.click(screen.getByRole("button", { name: "Save changes" }))
      expect(api.updateWorkspaceSettings).not.toHaveBeenCalled()
    },
  )

  it("retries the read after it fails", async () => {
    vi.mocked(api.getWorkspaceSettings)
      .mockRejectedValueOnce(
        new ApiError(
          500,
          "settings_unavailable",
          "Could not load the workspace settings.",
        ),
      )
      .mockResolvedValue(CAPS)
    render(<SettingsScreen />)

    expect(
      await screen.findByText("Could not load workspace settings"),
    ).toBeTruthy()

    fireEvent.click(screen.getByRole("button", { name: /try again/i }))

    expect((await capField("Concurrent sessions")).value).toBe("4")
  })

  it("confirms a landed save and re-reads what the server stored", async () => {
    renderSettings()

    fireEvent.change(await capField("Targets per installation"), {
      target: { value: "3" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }))

    expect(await screen.findByText("Settings saved.")).toBeTruthy()
    await waitFor(() => expect(api.getWorkspaceSettings).toHaveBeenCalledTimes(2))
  })

  it("keeps the server's own words when a save is refused", async () => {
    renderSettings()
    vi.mocked(api.updateWorkspaceSettings).mockRejectedValue(
      new ApiError(
        422,
        "validation_error",
        "maxTargetsPerInstallation: Input should be greater than or equal to 1",
      ),
    )

    fireEvent.change(await capField("Targets per installation"), {
      target: { value: "3" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }))

    expect((await screen.findByRole("alert")).textContent).toContain(
      "greater than or equal to 1",
    )
  })

  it("offers the repositories screen when that route is reachable", async () => {
    const onOpenRepositories = vi.fn()
    renderSettings({ onOpenRepositories })

    fireEvent.click(
      await screen.findByRole("button", { name: "Open repositories" }),
    )

    expect(onOpenRepositories).toHaveBeenCalledTimes(1)
  })
})
