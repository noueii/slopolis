import { setupServer } from "msw/node"
import { afterAll, beforeAll, describe, expect, it } from "vitest"

import type { UsageBreakdown, UsageResponse } from "@/api/contract"
import { dataset } from "./dataset"
import { usageHandlers } from "./usage"

const server = setupServer(...usageHandlers)

beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterAll(() => server.close())

async function fetchUsage(scenario?: string): Promise<UsageResponse> {
  const response = await fetch("/api/usage", {
    headers: scenario ? { "x-mock-scenario": scenario } : {},
  })
  return (await response.json()) as UsageResponse
}

function tokenSum(rows: UsageBreakdown[]): number {
  return rows.reduce((total, row) => total + row.tokens, 0)
}

function tokensBy(
  pick: (
    session: (typeof dataset.sessions)[number],
  ) => Array<[string, number]>,
): Map<string, number> {
  const totals = new Map<string, number>()
  for (const session of dataset.sessions) {
    for (const [key, tokens] of pick(session)) {
      if (tokens <= 0) continue
      totals.set(key, (totals.get(key) ?? 0) + tokens)
    }
  }
  return totals
}

describe("mock usage handler", () => {
  it("attributes every session's tokens across model, repository, and user", async () => {
    const usage = await fetchUsage()

    const recorded = dataset.sessions.filter(
      (session) => session.tokens > 0,
    ).length

    expect(usage.totalSessions).toBe(recorded)
    expect(usage.totalTokens).toBe(
      dataset.sessions.reduce((total, session) => total + session.tokens, 0),
    )
    expect(usage.totalCostUsd).toBeCloseTo(
      dataset.sessions.reduce((total, session) => total + session.costUsd, 0),
      6,
    )
    expect(tokenSum(usage.byModel)).toBe(usage.totalTokens)
    expect(tokenSum(usage.byRepository)).toBe(usage.totalTokens)
    expect(tokenSum(usage.byUser)).toBe(usage.totalTokens)
    expect(
      usage.series.reduce((total, point) => total + point.tokens, 0),
    ).toBe(usage.totalTokens)
    expect(
      usage.series.reduce((total, point) => total + point.sessions, 0),
    ).toBe(usage.totalSessions)

    expect(
      new Map(usage.byModel.map((row) => [row.key, row.tokens])),
    ).toEqual(
      tokensBy((session): Array<[string, number]> => [
        [session.model, session.tokens],
      ]),
    )
    expect(
      new Map(usage.byRepository.map((row) => [row.key, row.tokens])),
    ).toEqual(
      tokensBy((session): Array<[string, number]> =>
        session.targets.map((target) => [
          target.repository.fullName,
          target.tokens,
        ]),
      ),
    )
    expect(
      new Map(usage.byUser.map((row) => [row.key, row.tokens])),
    ).toEqual(
      tokensBy((session): Array<[string, number]> => [
        [session.triggeredBy.id, session.tokens],
      ]),
    )
  })

  it("uses the server's key and label convention for every dimension", async () => {
    const usage = await fetchUsage()

    expect(new Map(usage.byModel.map((row) => [row.key, row.label]))).toEqual(
      new Map(dataset.sessions.map((session) => [session.model, session.provider])),
    )
    expect(
      new Map(usage.byUser.map((row) => [row.key, row.label])),
    ).toEqual(
      new Map(
        dataset.sessions.map((session) => [
          session.triggeredBy.id,
          session.triggeredBy.handle,
        ]),
      ),
    )
    expect(usage.byRepository.map((row) => row.label)).toEqual(
      usage.byRepository.map((row) => row.key),
    )
    // Records that resolve to nobody are kept as a single bucket on the server,
    // never dropped, so no mock row may be a bare handle.
    expect(usage.byUser.every((row) => row.key.startsWith("usr_"))).toBe(true)
  })

  it("counts only sessions with recorded tokens in a bucket", async () => {
    const usage = await fetchUsage()

    for (const rows of [usage.byModel, usage.byRepository, usage.byUser]) {
      for (const row of rows) {
        expect(row.tokens).toBeGreaterThan(0)
        expect(row.sessions).toBeGreaterThan(0)
        expect(row.sessions).toBeLessThanOrEqual(usage.totalSessions)
        // Summing floats would otherwise leak long decimals the server rounds.
        expect(row.costUsd).toBe(Number(row.costUsd.toFixed(6)))
      }
    }
  })

  it("orders every breakdown by tokens, then key", async () => {
    const usage = await fetchUsage()

    for (const rows of [usage.byModel, usage.byRepository, usage.byUser]) {
      const ordered = [...rows].sort(
        (a, b) => b.tokens - a.tokens || a.key.localeCompare(b.key),
      )
      expect(rows).toEqual(ordered)
    }
  })

  it("buckets the series into one point per day that recorded usage", async () => {
    const usage = await fetchUsage()

    const recorded = dataset.sessions.filter((session) => session.tokens > 0)
    const recordedDays = [
      ...new Set(recorded.map((session) => session.createdAt.slice(0, 10))),
    ].sort()

    expect(usage.series.map((point) => point.date)).toEqual(recordedDays)
    for (const point of usage.series) {
      const sessions = recorded.filter(
        (session) => session.createdAt.slice(0, 10) === point.date,
      )
      expect(point.sessions).toBe(sessions.length)
      expect(point.tokens).toBe(
        sessions.reduce((total, session) => total + session.tokens, 0),
      )
      expect(point.costUsd).toBe(Number(point.costUsd.toFixed(6)))
    }
  })

  it("zeroes the whole report under the empty scenario", async () => {
    expect(await fetchUsage("empty")).toEqual({
      totalTokens: 0,
      totalCostUsd: 0,
      totalSessions: 0,
      byModel: [],
      byRepository: [],
      byUser: [],
      series: [],
    })
  })

  it("fails the request under the error scenario", async () => {
    const response = await fetch("/api/usage", {
      headers: { "x-mock-scenario": "error" },
    })

    expect(response.status).toBe(500)
    const body = (await response.json()) as { error: { code: string } }
    expect(body.error.code).toBe("usage_unavailable")
  })
})
