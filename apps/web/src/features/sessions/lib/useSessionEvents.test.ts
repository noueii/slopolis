import { act, cleanup, renderHook } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { api } from "@/api/client"
import type { ReviewSession } from "@/api/contract"
import { useSessionEvents } from "./useSessionEvents"

vi.mock("@/api/client", () => ({
  api: { getSession: vi.fn() },
}))

type Listener = (event: Event) => void

class MockEventSource {
  static instances: MockEventSource[] = []

  readonly url: string
  readonly listeners = new Map<string, Set<Listener>>()
  onerror: ((event: Event) => void) | null = null
  closed = false

  constructor(url: string) {
    this.url = url
    MockEventSource.instances.push(this)
  }

  addEventListener(type: string, listener: Listener): void {
    const set = this.listeners.get(type) ?? new Set<Listener>()
    set.add(listener)
    this.listeners.set(type, set)
  }

  removeEventListener(type: string, listener: Listener): void {
    this.listeners.get(type)?.delete(listener)
  }

  close(): void {
    this.closed = true
  }

  emit(type: string, data: string): void {
    const event = { data } as MessageEvent<string>
    for (const listener of this.listeners.get(type) ?? []) listener(event)
  }

  fail(): void {
    this.onerror?.(new Event("error"))
  }
}

const OriginalEventSource = globalThis.EventSource

function makeSession(overrides: Partial<ReviewSession> = {}): ReviewSession {
  return {
    id: "ses_1",
    title: "Guard token refresh skew",
    name: "acme/api-gateway#142",
    status: "running",
    model: "claude-sonnet-4",
    provider: "Anthropic",
    triggeredBy: {
      id: "usr_octocat",
      handle: "octocat",
      name: "Mona Lisa",
      isAdmin: false,
    },
    createdAt: "2026-01-01T00:00:00.000Z",
    targets: [
      {
        id: "t1",
        repository: {
          id: "r1",
          fullName: "acme/api-gateway",
          private: true,
          defaultBranch: "main",
        },
        number: 142,
        title: "fix: guard token refresh",
        url: "https://github.com/acme/api-gateway/pull/142",
        headBranch: "fix/token-refresh",
        status: "running",
        findingsCount: 0,
        tokens: 0,
        costUsd: 0,
      },
    ],
    targetCount: 1,
    tokens: 0,
    costUsd: 0,
    findingsCount: 0,
    ...overrides,
  }
}

describe("useSessionEvents", () => {
  beforeEach(() => {
    MockEventSource.instances = []
    globalThis.EventSource = MockEventSource as unknown as typeof EventSource
  })

  afterEach(() => {
    cleanup()
    vi.useRealTimers()
    if (OriginalEventSource) {
      globalThis.EventSource = OriginalEventSource
    } else {
      delete (globalThis as { EventSource?: unknown }).EventSource
    }
  })

  it("opens the session stream and delivers live status projections", () => {
    const onUpdate = vi.fn()
    const { result, unmount } = renderHook(() =>
      useSessionEvents("ses_1", { onUpdate }),
    )

    expect(MockEventSource.instances).toHaveLength(1)
    expect(MockEventSource.instances[0].url).toContain(
      "/api/sessions/ses_1/events",
    )

    act(() => {
      MockEventSource.instances[0].emit(
        "session",
        JSON.stringify({
          id: "ses_1",
          status: "running",
          targets: [{ id: "t1", number: 142, status: "running" }],
        }),
      )
    })

    expect(onUpdate).toHaveBeenCalledWith({
      status: "running",
      targets: [{ id: "t1", status: "running" }],
    })
    expect(result.current).toBe("live")
    unmount()
  })

  it("falls back to polling when the stream errors", async () => {
    vi.useFakeTimers()
    vi.mocked(api.getSession).mockResolvedValue(
      makeSession({ status: "running" }),
    )
    const onUpdate = vi.fn()
    const { result, unmount } = renderHook(() =>
      useSessionEvents("ses_2", { onUpdate }),
    )

    act(() => {
      MockEventSource.instances[0].fail()
    })
    expect(MockEventSource.instances[0].closed).toBe(true)
    expect(result.current).toBe("polling")

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2500)
    })

    expect(api.getSession).toHaveBeenCalledWith("ses_2")
    expect(onUpdate).toHaveBeenCalledWith({
      status: "running",
      targets: [{ id: "t1", status: "running" }],
    })
    unmount()
  })

  it("stays idle for a null session id", () => {
    const onUpdate = vi.fn()
    const { result, unmount } = renderHook(() =>
      useSessionEvents(null, { onUpdate }),
    )

    expect(MockEventSource.instances).toHaveLength(0)
    expect(result.current).toBe("idle")
    unmount()
  })
})
