import type { HttpHandler } from "msw"
import { afterEach, describe, expect, it, vi } from "vitest"

import {
  activeHandlers,
  alwaysOnHandlers,
  featureHandlers,
  handlers,
  selectHandlers,
  templatesMockHandlers,
} from "./handlers"
import { inboxHandlers } from "./pullRequests"

function expectAlwaysOn(selected: HttpHandler[]): void {
  const alwaysOn = new Set<unknown>(alwaysOnHandlers)
  expect(selected).toHaveLength(alwaysOnHandlers.length)
  for (const handler of selected) {
    expect(alwaysOn.has(handler)).toBe(true)
  }
}

function expectIncludesFeatures(selected: HttpHandler[]): void {
  const featureSet = new Set<unknown>(featureHandlers)
  expect(selected).toHaveLength(handlers.length)
  for (const handler of featureHandlers) {
    expect(selected.includes(handler)).toBe(true)
  }
  expect(featureSet.size).toBeGreaterThan(0)
}

describe("mock handler gating", () => {
  afterEach(() => {
    vi.unstubAllEnvs()
  })

  it("selects only the unbacked features' handlers when mocks are disabled", () => {
    expectAlwaysOn(selectHandlers(false))
  })

  it("selects every handler when mocks are enabled", () => {
    expectIncludesFeatures(selectHandlers(true))
  })

  it("registers no feature mocks under VITE_MOCK=off", () => {
    vi.stubEnv("VITE_MOCK", "off")
    expectAlwaysOn(activeHandlers())
  })

  it("keeps only the template mocks under VITE_MOCK=off", () => {
    vi.stubEnv("VITE_MOCK", "off")

    const selected = activeHandlers()
    for (const handler of templatesMockHandlers) {
      expect(selected.includes(handler)).toBe(true)
    }
    for (const handler of featureHandlers) {
      expect(selected.includes(handler)).toBe(false)
    }
  })

  it("bypasses the inbox mock under VITE_MOCK=off", () => {
    vi.stubEnv("VITE_MOCK", "off")

    // The inbox has a real endpoint now: serving it from the mock while the
    // real API is configured would show invented pull requests (spec v3 §7).
    for (const handler of inboxHandlers) {
      expect(activeHandlers().includes(handler)).toBe(false)
    }
  })

  it("registers feature mocks under VITE_MOCK=worker", () => {
    vi.stubEnv("VITE_MOCK", "worker")
    expectIncludesFeatures(activeHandlers())
  })
})
