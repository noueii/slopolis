import { cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { ApiError, api } from "@/api/client"
import type * as client from "@/api/client"
import type { UsageResponse } from "@/api/contract"
import { UsageScreen } from "./UsageScreen"

vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof client>()
  return {
    githubAppInstallUrl: actual.githubAppInstallUrl,
    api: {
      getUsage: vi.fn(),
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

/** The server keys a user bucket by the user's id and labels it with the handle. */
const USER_ID = "8f2a1c1e-6b0a-4b3e-9c1d-2f4a5b6c7d8e"

const usage: UsageResponse = {
  totalTokens: 480_000,
  totalCostUsd: 7.2,
  totalSessions: 6,
  byModel: [
    {
      key: "claude-sonnet-4-5",
      label: "Claude Sonnet 4.5",
      tokens: 300_000,
      costUsd: 1.8,
      sessions: 4,
    },
    {
      key: "gpt-5-mini",
      label: "GPT-5 mini",
      tokens: 180_000,
      costUsd: 5.4,
      sessions: 2,
    },
  ],
  byRepository: [
    {
      key: "acme/api-gateway",
      label: "acme/api-gateway",
      tokens: 480_000,
      costUsd: 7.2,
      sessions: 6,
    },
  ],
  byUser: [
    { key: USER_ID, label: "noueii", tokens: 480_000, costUsd: 7.2, sessions: 6 },
  ],
  series: [
    { date: "2026-09-01", tokens: 120_000, costUsd: 1.8, sessions: 2 },
    { date: "2026-09-02", tokens: 240_000, costUsd: 3.6, sessions: 3 },
    { date: "2026-09-03", tokens: 120_000, costUsd: 1.8, sessions: 1 },
  ],
}

const emptyUsage: UsageResponse = {
  totalTokens: 0,
  totalCostUsd: 0,
  totalSessions: 0,
  byModel: [],
  byRepository: [],
  byUser: [],
  series: [],
}

afterEach(cleanup)

describe("UsageScreen", () => {
  it("reports the totals, the daily series, and every breakdown", async () => {
    vi.mocked(api.getUsage).mockResolvedValue(usage)

    render(<UsageScreen onNewReview={vi.fn()} />)

    const totals = await screen.findByRole("region", { name: "Usage totals" })
    expect(within(totals).getByText("480k")).toBeDefined()
    expect(within(totals).getByText("$7.20")).toBeDefined()
    expect(within(totals).getByText("6")).toBeDefined()

    // The daily buckets stay readable without the bars.
    expect(
      screen.getByRole("img", { name: /^Daily usage from Sep 1 to Sep 3:/ }),
    ).toBeDefined()
    const daily = screen.getByRole("table", { name: "Daily usage" })
    expect(within(daily).getAllByRole("row")).toHaveLength(4)
    const peakDay = within(daily).getByRole("rowheader", { name: "Sep 2" })
    const peakRow = peakDay.closest("tr")
    if (!peakRow) throw new Error("the Sep 2 bucket is not in a table row")
    expect(within(peakRow).getByText("240k")).toBeDefined()
    expect(within(peakRow).getByText("$3.60")).toBeDefined()

    const models = screen.getByRole("table", { name: "By model" })
    expect(within(models).getAllByRole("row")).toHaveLength(3)
    expect(
      within(models).getByRole("rowheader", { name: /Claude Sonnet 4\.5/ }),
    ).toBeDefined()
    expect(within(models).getByText("300k")).toBeDefined()
    expect(within(models).getByText("$5.40")).toBeDefined()
    expect(within(models).getByText("75%")).toBeDefined()
    // The API orders by tokens, but the share bar measures cost: rows follow the
    // bars, so the biggest share of spend leads even when it used fewer tokens.
    const modelOrder = within(models)
      .getAllByRole("rowheader")
      .map((cell) => cell.textContent)
    expect(modelOrder).toEqual([
      expect.stringContaining("GPT-5 mini"),
      expect.stringContaining("Claude Sonnet 4.5"),
    ])

    const repositories = screen.getByRole("table", { name: "By repository" })
    expect(
      within(repositories).getByRole("rowheader", { name: "acme/api-gateway" }),
    ).toBeDefined()

    const users = screen.getByRole("table", { name: "By user" })
    expect(within(users).getByRole("rowheader", { name: "noueii" })).toBeDefined()
    expect(within(users).getByText("100%")).toBeDefined()
    // The key is a machine id with nothing for a reader to act on, so the row
    // is the handle alone — unlike the model rows above, which keep their ids.
    expect(within(users).queryByText(USER_ID)).toBeNull()
    expect(within(models).getByText("claude-sonnet-4-5")).toBeDefined()
  })

  it("says which section has nothing attributed yet instead of hiding it", async () => {
    // The server adds the per-user roll-up after the other dimensions, so a
    // workspace can legitimately answer with an empty `byUser`.
    vi.mocked(api.getUsage).mockResolvedValue({ ...usage, byUser: [] })

    render(<UsageScreen onNewReview={vi.fn()} />)

    const users = await screen.findByRole("table", { name: "By user" })
    expect(
      within(users).getByText("No usage attributed to a user yet."),
    ).toBeDefined()
    expect(screen.getByRole("region", { name: "By user" })).toBeDefined()
  })

  it("offers to start a review when nothing has been recorded", async () => {
    vi.mocked(api.getUsage).mockResolvedValue(emptyUsage)
    const onNewReview = vi.fn()

    render(<UsageScreen onNewReview={onNewReview} />)

    expect(await screen.findByText("No usage recorded yet")).toBeDefined()
    expect(screen.queryByRole("table", { name: "By model" })).toBeNull()

    fireEvent.click(screen.getByRole("button", { name: "Start a review" }))
    expect(onNewReview).toHaveBeenCalledTimes(1)
  })

  it("shows the failure and loads the report again on retry", async () => {
    vi.mocked(api.getUsage)
      .mockRejectedValueOnce(
        new ApiError(500, "usage_unavailable", "Could not load usage stats."),
      )
      .mockResolvedValue(usage)

    render(<UsageScreen onNewReview={vi.fn()} />)

    expect(await screen.findByText("Could not load usage")).toBeDefined()
    expect(screen.getByText("Could not load usage stats.")).toBeDefined()

    fireEvent.click(screen.getByRole("button", { name: "Try again" }))

    expect(await screen.findByRole("table", { name: "By model" })).toBeDefined()
    expect(api.getUsage).toHaveBeenCalledTimes(2)
  })
})
