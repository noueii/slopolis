/**
 * The manual retry action (spec 10.5 §Manual retry).
 *
 * What matters here is what a reviewer of a failed session sees and can do:
 * the action is offered exactly when a target can be re-run, it says whether
 * the retry reposts the review already on hand or runs the model again, it
 * posts one retry for the session, the API's own refusal is the message on
 * screen, and a session that comes back queued reads that way without a reload.
 */

import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { ApiError, api } from "@/api/client"
import type * as client from "@/api/client"
import type {
  ReviewSession,
  SessionStatus,
  SessionTarget,
  TargetStatus,
} from "@/api/contract"
import { SessionDetail } from "./SessionDetail"

vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof client>()
  return {
    githubAppInstallUrl: actual.githubAppInstallUrl,
    ApiError: actual.ApiError,
    isMockModeEnabled: () => false,
    api: {
      getSession: vi.fn(),
      getRunTree: vi.fn(),
      retrySession: vi.fn(),
    },
  }
})

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

  emit(type: string, data: unknown): void {
    const event = { data: JSON.stringify(data) } as MessageEvent<string>
    for (const listener of this.listeners.get(type) ?? []) listener(event)
  }
}

const OriginalEventSource = globalThis.EventSource
const SESSION_ID = "ses_failed"

function target(
  id: string,
  status: TargetStatus,
  retryAction?: SessionTarget["retryAction"],
): SessionTarget {
  return {
    id,
    repository: {
      id: "repo_1",
      fullName: "acme/api-gateway",
      private: true,
      defaultBranch: "main",
    },
    number: 142,
    title: "fix: guard token refresh",
    url: "https://github.com/acme/api-gateway/pull/142",
    headBranch: "fix/token-refresh",
    status,
    retryAction,
    findingsCount: 2,
    tokens: 18_000,
    costUsd: 0.08,
  }
}

/**
 * A status, plus the retry action the server attaches to it when the test cares
 * about what the retry will do.
 */
type TargetSpec =
  | TargetStatus
  | { status: TargetStatus; retryAction: SessionTarget["retryAction"] }

function sessionWith(
  status: SessionStatus,
  specs: TargetSpec[],
): ReviewSession {
  return {
    id: SESSION_ID,
    title: "Guard token refresh skew",
    name: "acme/api-gateway#142",
    status,
    model: "claude-sonnet-4",
    provider: "Anthropic",
    triggeredBy: {
      id: "usr_octocat",
      handle: "octocat",
      name: "Mona Lisa",
      isAdmin: false,
    },
    createdAt: "2026-09-20T09:00:00.000Z",
    targets: specs.map((spec, index) => {
      const { status: targetStatus, retryAction } =
        typeof spec === "string" ? { status: spec, retryAction: undefined } : spec
      return target(`tgt_${index + 1}`, targetStatus, retryAction)
    }),
    targetCount: specs.length,
    tokens: 18_000,
    costUsd: 0.08,
    findingsCount: 2,
  }
}

/** One target failed, one finished: the shape the action exists for. */
const FAILED = sessionWith("failed", ["failed", "done"])
/** The last attempt bought the review and only failed to publish it. */
const FAILED_PUBLISH = sessionWith("failed", [
  { status: "failed", retryAction: "publish" },
  "done",
])
/** The model has to run again before anything can be published. */
const FAILED_REVIEW = sessionWith("failed", [
  { status: "failed", retryAction: "review" },
  "done",
])
/** The model has to run again for the cancelled one, so the action says so. */
const MIXED_RETRY = sessionWith("failed", [
  { status: "failed", retryAction: "publish" },
  { status: "cancelled", retryAction: "review" },
])
const ALL_DONE = sessionWith("done", [
  { status: "done", retryAction: null },
  { status: "done", retryAction: null },
])
const REQUEUED = sessionWith("queued", ["queued", "done"])

const RETRY = "Retry failed targets"
const RETRY_PUBLISH = "Retry publishing"
const NO_NEW_ANALYSIS = /no new analysis runs/

function header(): HTMLElement {
  const found = screen.getByRole("heading", { name: "acme/api-gateway#142" })
    .parentElement
  if (!found) throw new Error("the session header is missing")
  return found
}

/** The session detail with its tree empty, so the summary tab is the one shown. */
function renderDetail(session: ReviewSession) {
  vi.mocked(api.getSession).mockResolvedValue(session)
  vi.mocked(api.getRunTree).mockResolvedValue({ runs: [] })
  render(<SessionDetail sessionId={SESSION_ID} onBack={vi.fn()} />)
}

beforeEach(() => {
  MockEventSource.instances = []
  globalThis.EventSource = MockEventSource as unknown as typeof EventSource
})

afterEach(() => {
  cleanup()
  if (OriginalEventSource) {
    globalThis.EventSource = OriginalEventSource
  } else {
    delete (globalThis as { EventSource?: unknown }).EventSource
  }
})

describe("SessionDetail manual retry", () => {
  it("posts one retry for the session and holds the action while it runs", async () => {
    renderDetail(FAILED)
    // Never settles: this test is about the request and the pending state.
    vi.mocked(api.retrySession).mockReturnValue(
      new Promise<ReviewSession>(() => {}),
    )

    fireEvent.click(await screen.findByRole("button", { name: RETRY }))

    // No target selection: the session-level action retries every retryable one.
    expect(api.retrySession).toHaveBeenCalledTimes(1)
    expect(api.retrySession).toHaveBeenCalledWith(SESSION_ID)
    const pending = await screen.findByRole("button", { name: /Retrying/ })
    expect((pending as HTMLButtonElement).disabled).toBe(true)
  })

  it("leaves a session whose targets all finished without the action", async () => {
    renderDetail(ALL_DONE)

    await screen.findByRole("heading", { name: "acme/api-gateway#142" })

    expect(screen.queryByRole("button", { name: RETRY })).toBeNull()
    expect(screen.queryByRole("button", { name: RETRY_PUBLISH })).toBeNull()
  })

  it("says the retry will publish when every retryable target holds a review", async () => {
    renderDetail(FAILED_PUBLISH)

    // The server marked the failed target as a publish retry, so the action
    // promises no second model call rather than a silent one.
    const button = await screen.findByRole("button", { name: RETRY_PUBLISH })
    expect(screen.queryByRole("button", { name: RETRY })).toBeNull()
    expect(screen.getByText(NO_NEW_ANALYSIS)).toBeDefined()

    vi.mocked(api.retrySession).mockResolvedValue(FAILED_PUBLISH)
    fireEvent.click(button)
    await waitFor(() =>
      expect(api.retrySession).toHaveBeenCalledWith(SESSION_ID),
    )
  })

  it("keeps the review label when a retryable target needs the model", async () => {
    renderDetail(FAILED_REVIEW)

    await screen.findByRole("button", { name: RETRY })
    expect(screen.queryByRole("button", { name: RETRY_PUBLISH })).toBeNull()
    expect(screen.queryByText(NO_NEW_ANALYSIS)).toBeNull()
  })

  it("promises a review when only some of the retryable targets can publish", async () => {
    renderDetail(MIXED_RETRY)

    // One target still needs the model, so the action must not imply otherwise.
    await screen.findByRole("button", { name: RETRY })
    expect(screen.queryByRole("button", { name: RETRY_PUBLISH })).toBeNull()
    expect(screen.queryByText(NO_NEW_ANALYSIS)).toBeNull()
  })

  it("shows the server's refusal and frees the action again", async () => {
    renderDetail(FAILED)
    vi.mocked(api.retrySession).mockRejectedValue(
      new ApiError(
        409,
        "nothing_to_retry",
        "This session has no failed or cancelled targets to retry.",
      ),
    )

    fireEvent.click(await screen.findByRole("button", { name: RETRY }))

    const alert = await screen.findByRole("alert")
    expect(alert.textContent).toBe(
      "This session has no failed or cancelled targets to retry.",
    )
    const freed = screen.getByRole("button", { name: RETRY })
    expect((freed as HTMLButtonElement).disabled).toBe(false)
  })

  it("requeues the session and lets the reopened stream take over", async () => {
    vi.mocked(api.getSession)
      .mockResolvedValueOnce(FAILED)
      .mockResolvedValue(REQUEUED)
    vi.mocked(api.getRunTree).mockResolvedValue({ runs: [] })
    vi.mocked(api.retrySession).mockResolvedValue(REQUEUED)

    render(<SessionDetail sessionId={SESSION_ID} onBack={vi.fn()} />)

    await screen.findByRole("heading", { name: "acme/api-gateway#142" })
    expect(within(header()).getByText("Failed")).toBeDefined()

    fireEvent.click(screen.getByRole("button", { name: RETRY }))

    // The session and its tree are read again, and the new state needs no reload.
    await waitFor(() =>
      expect(within(header()).getByText("Queued")).toBeDefined(),
    )
    expect(api.getSession).toHaveBeenCalledTimes(2)
    expect(api.getRunTree).toHaveBeenCalledTimes(2)

    // The new attempt streams its own status over the requeued one.
    expect(MockEventSource.instances).toHaveLength(2)
    act(() => {
      MockEventSource.instances[1].emit("session", {
        id: SESSION_ID,
        status: "running",
        targets: [{ id: "tgt_1", number: 142, status: "running" }],
      })
    })
    expect(within(header()).getByText("Running")).toBeDefined()
  })
})
