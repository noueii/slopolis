import type { HttpHandler } from "msw"
import { afterEach, describe, expect, it, vi } from "vitest"

import {
  activeHandlers,
  featureHandlers,
  handlers,
  selectHandlers,
  templatesMockHandlers,
} from "./handlers"

function expectOnlyTemplates(selected: HttpHandler[]): void {
  const templateSet = new Set<unknown>(templatesMockHandlers)
  expect(selected).toHaveLength(templatesMockHandlers.length)
  for (const handler of selected) {
    expect(templateSet.has(handler)).toBe(true)
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

  it("selects only template handlers when mocks are disabled", () => {
    expectOnlyTemplates(selectHandlers(false))
  })

  it("selects every handler when mocks are enabled", () => {
    expectIncludesFeatures(selectHandlers(true))
  })

  it("registers no feature mocks under VITE_MOCK=off", () => {
    vi.stubEnv("VITE_MOCK", "off")
    expectOnlyTemplates(activeHandlers())
  })

  it("registers feature mocks under VITE_MOCK=worker", () => {
    vi.stubEnv("VITE_MOCK", "worker")
    expectIncludesFeatures(activeHandlers())
  })
})
