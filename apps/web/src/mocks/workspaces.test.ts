/**
 * The mock workspace-settings store: what it serves to the Settings screen,
 * held to the semantics the real router implements — an unset cap is unlimited,
 * a PATCH is partial (an omitted cap keeps its value, an explicit `null` clears
 * it), and the edge refuses anything that is not `null` or an integer >= 1.
 */

import { setupServer } from "msw/node"
import { afterAll, beforeAll, beforeEach, describe, expect, it } from "vitest"

import type { ApiErrorBody, WorkspaceSettings } from "@/api/contract"
import { resetWorkspaceSettingsStore, workspaceHandlers } from "./workspaces"

const server = setupServer(...workspaceHandlers)

beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterAll(() => server.close())
beforeEach(() => resetWorkspaceSettingsStore())

/** A workspace that has set no cap, which is every workspace to begin with. */
const UNLIMITED: WorkspaceSettings = {
  maxConcurrentSessions: null,
  maxSessionsPerUserPerDay: null,
  maxTargetsPerRepo: null,
  maxTargetsPerInstallation: null,
}

async function send(
  init: RequestInit = {},
  scenario?: string,
): Promise<Response> {
  const headers = new Headers(init.headers)
  if (init.body !== undefined) headers.set("Content-Type", "application/json")
  if (scenario !== undefined) headers.set("x-mock-scenario", scenario)
  return fetch("/api/workspaces/settings", { ...init, headers })
}

async function readSettings(): Promise<WorkspaceSettings> {
  const response = await send()
  expect(response.ok).toBe(true)
  return (await response.json()) as WorkspaceSettings
}

function patch(body: unknown, scenario?: string): Promise<Response> {
  return send({ method: "PATCH", body: JSON.stringify(body) }, scenario)
}

async function refusal(response: Response): Promise<ApiErrorBody> {
  expect(response.status).toBe(422)
  const body = (await response.json()) as ApiErrorBody
  expect(body.error.code).toBe("validation_error")
  return body
}

describe("mock workspace settings", () => {
  it("starts with every cap unset", async () => {
    expect(await readSettings()).toEqual(UNLIMITED)
  })

  it("round-trips a PATCH and leaves the caps it did not mention alone", async () => {
    const response = await patch({
      maxConcurrentSessions: 4,
      maxSessionsPerUserPerDay: null,
      maxTargetsPerRepo: 2,
    })
    expect(response.status).toBe(200)
    expect(await response.json()).toEqual({
      ...UNLIMITED,
      maxConcurrentSessions: 4,
      maxTargetsPerRepo: 2,
    })

    // The store is what the next read serves, not just what the PATCH echoed.
    expect(await readSettings()).toEqual({
      ...UNLIMITED,
      maxConcurrentSessions: 4,
      maxTargetsPerRepo: 2,
    })

    // An explicit null clears one cap and touches nothing else.
    expect((await patch({ maxConcurrentSessions: null })).status).toBe(200)
    expect(await readSettings()).toEqual({ ...UNLIMITED, maxTargetsPerRepo: 2 })
  })

  it("refuses a cap that is not null or an integer of 1 or more", async () => {
    for (const value of [0, -1, 2.5, "3", true]) {
      const body = await refusal(await patch({ maxConcurrentSessions: value }))
      expect(body.error.detail).toContain("maxConcurrentSessions")
    }
    // A refused PATCH wrote nothing.
    expect(await readSettings()).toEqual(UNLIMITED)
  })

  it("refuses a field the schema does not know", async () => {
    const body = await refusal(
      await patch({ maxConcurrentSessionsPerWeek: 3 }),
    )
    expect(body.error.detail).toContain("maxConcurrentSessionsPerWeek")
    expect(await readSettings()).toEqual(UNLIMITED)
  })

  it("serves the unfilled store under the empty scenario", async () => {
    await patch({ maxConcurrentSessions: 7 })
    const response = await send({}, "empty")
    expect(await response.json()).toEqual(UNLIMITED)
  })

  it("fails both routes under the error scenario, writing nothing", async () => {
    expect((await send({}, "error")).status).toBe(500)
    expect((await patch({ maxConcurrentSessions: 1 }, "error")).status).toBe(500)
    expect(await readSettings()).toEqual(UNLIMITED)
  })
})
