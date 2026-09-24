import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { api } from "@/api/client"
import type * as client from "@/api/client"
import type {
  AgentEventItem,
  AgentEventPage,
  AgentRunNode,
  ReviewSession,
  SessionTarget,
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
      getRunEvents: vi.fn(),
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

  fail(): void {
    this.onerror?.(new Event("error"))
  }
}

const OriginalEventSource = globalThis.EventSource
const SESSION_ID = "ses_1"

const TARGET: SessionTarget = {
  id: "tgt_1",
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
  status: "done",
  findingsCount: 2,
  tokens: 18_000,
  costUsd: 0.08,
}

function session(overrides: Partial<ReviewSession> = {}): ReviewSession {
  return {
    id: SESSION_ID,
    title: "Guard token refresh skew",
    name: "acme/api-gateway#142",
    status: "done",
    model: "claude-sonnet-4",
    provider: "Anthropic",
    triggeredBy: {
      id: "usr_octocat",
      handle: "octocat",
      name: "Mona Lisa",
      isAdmin: false,
    },
    createdAt: "2026-09-20T09:00:00.000Z",
    targets: [TARGET],
    targetCount: 1,
    tokens: 18_000,
    costUsd: 0.08,
    findingsCount: 2,
    ...overrides,
  }
}

function run(overrides: Partial<AgentRunNode> & Pick<AgentRunNode, "id">): AgentRunNode {
  return {
    sessionId: SESSION_ID,
    targetId: null,
    parentRunId: null,
    level: "sub",
    role: "reviewer",
    modelId: "claude-sonnet-4",
    objective: "Review something",
    status: "done",
    tokens: 0,
    costUsd: 0,
    startedAt: "2026-09-20T09:00:05.000Z",
    endedAt: "2026-09-20T09:00:20.000Z",
    error: null,
    children: [],
    ...overrides,
  }
}

const FAILED_SUB = run({
  id: "run_sub_failed",
  parentRunId: "run_pr",
  targetId: "tgt_1",
  role: "security-reviewer",
  objective: "Check auth boundaries and secret handling in #142",
  status: "failed",
  tokens: 2_100,
  costUsd: 0.01,
  error: "provider returned an empty completion",
})

const DONE_SUB = run({
  id: "run_sub_done",
  parentRunId: "run_pr",
  targetId: "tgt_1",
  role: "logic-reviewer",
  objective: "Review control flow and edge cases in #142",
  tokens: 4_800,
  costUsd: 0.02,
})

/** Session done, PR done, and one sub-agent that failed on the way. */
const SETTLED_TREE: AgentRunNode[] = [
  run({
    id: "run_main",
    level: "main",
    role: "orchestrator.main",
    objective: "Coordinate the review of every target in this session",
    status: "done",
    tokens: 1_200,
    costUsd: 0.01,
    children: [
      run({
        id: "run_pr",
        parentRunId: "run_main",
        targetId: "tgt_1",
        level: "pr",
        role: "orchestrator.pr",
        objective: "Review PR #142: fix: guard token refresh",
        status: "done",
        tokens: 5_400,
        costUsd: 0.02,
        children: [DONE_SUB, FAILED_SUB],
      }),
    ],
  }),
]

const RUNNING_SUB = run({
  id: "run_sub_running",
  parentRunId: "run_pr",
  targetId: "tgt_1",
  role: "logic-reviewer",
  objective: "Review control flow and edge cases in #142",
  status: "running",
  tokens: 900,
  costUsd: 0.004,
  endedAt: null,
})

/**
 * After a retry (spec 10.5): the queue owns the session again, so no run is
 * live, but the runs the failed attempt left are still what the tree serves.
 */
const REQUEUED_TREE: AgentRunNode[] = [
  run({
    id: "run_main",
    level: "main",
    role: "orchestrator.main",
    objective: "Coordinate the review of every target in this session",
    status: "failed",
    error: "provider unreachable after retry",
    tokens: 1_200,
    costUsd: 0.01,
    children: [
      run({
        id: "run_pr",
        parentRunId: "run_main",
        targetId: "tgt_1",
        level: "pr",
        role: "orchestrator.pr",
        objective: "Review PR #142: fix: guard token refresh",
        status: "failed",
        error: "sub-agent returned no findings",
        tokens: 5_400,
        costUsd: 0.02,
        children: [FAILED_SUB],
      }),
    ],
  }),
]

/** Same shape while the session is still working. */
const LIVE_TREE: AgentRunNode[] = [
  run({
    id: "run_main",
    level: "main",
    role: "orchestrator.main",
    objective: "Coordinate the review of every target in this session",
    status: "running",
    tokens: 400,
    costUsd: 0.002,
    endedAt: null,
    children: [
      run({
        id: "run_pr",
        parentRunId: "run_main",
        targetId: "tgt_1",
        level: "pr",
        role: "orchestrator.pr",
        objective: "Review PR #142: fix: guard token refresh",
        status: "running",
        tokens: 2_400,
        costUsd: 0.01,
        endedAt: null,
        children: [RUNNING_SUB],
      }),
    ],
  }),
]

function event(
  overrides: Partial<AgentEventItem> & Pick<AgentEventItem, "id" | "runId" | "seq" | "type">,
): AgentEventItem {
  return {
    parentRunId: null,
    payload: {},
    createdAt: "2026-09-20T09:00:10.000Z",
    ...overrides,
  }
}

const FAILED_SUB_EVENTS: AgentEventItem[] = [
  event({
    id: "e1",
    runId: "run_sub_failed",
    parentRunId: "run_pr",
    seq: 1,
    type: "agent.spawned",
    payload: {
      role: "security-reviewer",
      level: "sub",
      parent_run_id: "run_pr",
      target_id: "tgt_1",
      objective: "Check auth boundaries and secret handling in #142",
      model_role: "review",
      depth: 2,
    },
  }),
  event({
    id: "e2",
    runId: "run_sub_failed",
    parentRunId: "run_pr",
    seq: 2,
    type: "agent.started",
    payload: {
      role: "security-reviewer",
      model_id: "claude-sonnet-4",
      status: "running",
    },
  }),
  event({
    id: "e3",
    runId: "run_sub_failed",
    parentRunId: "run_pr",
    seq: 3,
    type: "agent.step",
    payload: {
      step: 1,
      model_id: "claude-sonnet-4",
      summary: "Reading the changed files for acme/api-gateway#142.",
      tool_call_count: 1,
    },
  }),
  event({
    id: "e4",
    runId: "run_sub_failed",
    parentRunId: "run_pr",
    seq: 4,
    type: "agent.failed",
    payload: {
      status: "failed",
      summary: "Stopped after the model returned nothing usable",
      error: "provider returned an empty completion",
      tokens_used: 2_100,
      step_count: 2,
      internal_trace: "the model transcript",
    },
  }),
]

function page(items: AgentEventItem[], nextSeq: number | null): AgentEventPage {
  return { items, nextSeq }
}

function mockApi(
  runs: AgentRunNode[],
  sessionOverrides: Partial<ReviewSession> = {},
) {
  vi.mocked(api.getSession).mockResolvedValue(session(sessionOverrides))
  vi.mocked(api.getRunTree).mockResolvedValue({ runs })
  vi.mocked(api.getRunEvents).mockResolvedValue(page([], null))
}

/** Serve a page per run id, so selecting a node loads that run's log. */
function mockRunEvents(events: Record<string, AgentEventPage>) {
  vi.mocked(api.getRunEvents).mockImplementation(async (_sessionId, runId) =>
    events[runId] ?? page([], null),
  )
}

function runTree(): HTMLElement {
  return screen.getByRole("region", { name: "Agent run tree" })
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

describe("SessionDetail run tree", () => {
  it("renders every run with its status and leaves a failed sub-agent off the session", async () => {
    mockApi(SETTLED_TREE)

    render(<SessionDetail sessionId={SESSION_ID} onBack={vi.fn()} />)

    const tree = await screen.findByRole("region", { name: "Agent run tree" })
    expect(within(tree).getByText("4 runs · 1 failed")).toBeDefined()
    expect(
      within(tree).getByRole("button", {
        name: /orchestrator\.main — Coordinate the review/,
      }),
    ).toBeDefined()
    expect(
      within(tree).getByRole("button", { name: /orchestrator\.pr — Review PR #142/ }),
    ).toBeDefined()
    expect(
      within(tree).getByRole("button", { name: /logic-reviewer — Review control flow/ }),
    ).toBeDefined()
    const failedRow = within(tree)
      .getByRole("button", { name: /security-reviewer — Check auth boundaries/ })
      .closest("li")
    if (!failedRow) throw new Error("the failed sub-agent is not in a row")
    expect(within(failedRow).getByText("Failed")).toBeDefined()

    // The session's own status is unaffected by the failed sub-agent.
    const header = screen.getByText("acme/api-gateway#142").closest("header")
    if (!header) throw new Error("the session header is missing")
    expect(within(header).getByText("Done")).toBeDefined()
    expect(within(header).queryByText("Failed")).toBeNull()

    // A settled session's runs are its result, not history, and nothing is
    // waiting on a worker.
    expect(within(tree).queryByText(/previous attempt/i)).toBeNull()
    expect(screen.queryByText(/previous attempt/i)).toBeNull()
    expect(screen.queryByText(/Waiting for a worker/)).toBeNull()
    expect(screen.queryByText(/reopens this run/)).toBeNull()
  })

  it("shows the selected run's events in words, without dumping the payload", async () => {
    mockApi(SETTLED_TREE)
    mockRunEvents({ run_sub_failed: page(FAILED_SUB_EVENTS, null) })

    render(<SessionDetail sessionId={SESSION_ID} onBack={vi.fn()} />)

    const tree = await screen.findByRole("region", { name: "Agent run tree" })
    fireEvent.click(
      within(tree).getByRole("button", {
        name: /security-reviewer — Check auth boundaries/,
      }),
    )

    const detail = await screen.findByRole("region", { name: "Run details" })
    expect(await within(detail).findByText(/^Reading the changed files/)).toBeDefined()
    // The reason reads in words — once as the run's error, once as the event.
    expect(
      within(detail).getAllByText("provider returned an empty completion").length,
    ).toBeGreaterThan(0)
    expect(within(detail).getByText(/does not fail the session/)).toBeDefined()
    expect(api.getRunEvents).toHaveBeenCalledWith(SESSION_ID, "run_sub_failed")
    // Everything rendered comes from the allow-listed fields, never the payload.
    expect(within(detail).queryByText(/internal_trace/)).toBeNull()
    expect(within(detail).queryByText(/the model transcript/)).toBeNull()
  })

  it("pages a run's events when the page comes back full", async () => {
    const firstPage = Array.from({ length: 200 }, (_, index) =>
      event({
        id: `long_${index + 1}`,
        runId: "run_main",
        seq: index + 1,
        type: "agent.step",
        payload: { step: index + 1, summary: `Step summary ${index + 1}` },
      }),
    )
    const secondPage = [
      event({
        id: "long_201",
        runId: "run_main",
        seq: 201,
        type: "agent.message",
        payload: { role: "assistant", summary: "Called the last step.", chars: 20 },
      }),
    ]
    mockApi(LIVE_TREE)
    vi.mocked(api.getRunEvents).mockImplementation(async (_sessionId, _runId, opts) =>
      opts?.afterSeq === 200 ? page(secondPage, 201) : page(firstPage, 200),
    )

    render(<SessionDetail sessionId={SESSION_ID} onBack={vi.fn()} />)

    const detail = await screen.findByRole("region", { name: "Run details" })
    const loadMore = await within(detail).findByRole("button", { name: "Load more" })
    expect(within(detail).getByText("Step summary 200")).toBeDefined()

    fireEvent.click(loadMore)

    expect(await within(detail).findByText("Called the last step.")).toBeDefined()
    expect(api.getRunEvents).toHaveBeenLastCalledWith(SESSION_ID, "run_main", {
      afterSeq: 200,
    })
  })

  it("folds a live agent event onto the selected node's status and usage", async () => {
    mockApi(LIVE_TREE)

    render(<SessionDetail sessionId={SESSION_ID} onBack={vi.fn()} />)

    const tree = await screen.findByRole("region", { name: "Agent run tree" })
    const row = within(tree)
      .getByRole("button", { name: /logic-reviewer — Review control flow/ })
      .closest("li")
    if (!row) throw new Error("the running sub-agent is not in a row")
    expect(within(row).getByText("Running")).toBeDefined()

    act(() => {
      MockEventSource.instances[0].emit("agent", {
        id: "live_1",
        runId: "run_sub_running",
        parentRunId: "run_pr",
        seq: 9,
        type: "agent.completed",
        payload: {
          status: "done",
          summary: "Returned 2 findings",
          tokens_used: 5_000,
          cost_usd: 0.02,
          finding_count: 2,
          step_count: 2,
        },
        createdAt: "2026-09-20T09:05:00.000Z",
      })
    })

    expect(within(row).getByText("Done")).toBeDefined()
    expect(within(row).getByText("5.0k")).toBeDefined()
    expect(within(row).getByText("$0.02")).toBeDefined()
  })

  it("reads a streamed turn's prompt in the node detail, without a reload", async () => {
    mockApi(LIVE_TREE)

    render(<SessionDetail sessionId={SESSION_ID} onBack={vi.fn()} />)

    const tree = await screen.findByRole("region", { name: "Agent run tree" })
    fireEvent.click(
      within(tree).getByRole("button", {
        name: /logic-reviewer — Review control flow/,
      }),
    )

    act(() => {
      MockEventSource.instances[0].emit("agent", {
        id: "live_turn",
        runId: "run_sub_running",
        parentRunId: "run_pr",
        seq: 9,
        type: "agent.turn",
        payload: {
          model_id: "claude-sonnet-4",
          messages: [
            { role: "system", content: "You review one pull request." },
            { role: "user", content: "Review the diff: +guard the input" },
          ],
          response: '{"findings": []}',
          prompt_tokens: 900,
          completion_tokens: 40,
          total_tokens: 940,
          cost_usd: 0.004,
          chars: 78,
          truncated: false,
        },
        createdAt: "2026-09-20T09:05:00.000Z",
      })
    })

    const detail = screen.getByRole("region", { name: "Run details" })
    expect(await within(detail).findByText(/Model turn/)).toBeDefined()

    fireEvent.click(within(detail).getByRole("button", { name: "Show prompt" }))

    expect(within(detail).getByText("You review one pull request.")).toBeDefined()
    expect(
      within(detail).getByText("Review the diff: +guard the input"),
    ).toBeDefined()
    expect(within(detail).getByText('{"findings": []}')).toBeDefined()
  })

  it("reads a requeued session's finished runs as the previous attempt", async () => {
    const createdAt = new Date(Date.now() - 4 * 60_000).toISOString()
    mockApi(REQUEUED_TREE, {
      status: "queued",
      createdAt,
      startedAt: createdAt,
      targets: [{ ...TARGET, status: "queued" }],
    })

    render(<SessionDetail sessionId={SESSION_ID} onBack={vi.fn()} />)

    // The contradiction this state used to read as: the header says queued
    // while the tree's runs still say failed.
    const tree = await screen.findByRole("region", { name: "Agent run tree" })
    expect(screen.getByText("Queued")).toBeDefined()
    expect(within(tree).getAllByText("Failed").length).toBeGreaterThan(0)

    // Each finished run is labelled as history rather than as this attempt.
    expect(within(tree).getAllByText("previous attempt")).toHaveLength(3)
    const mainButton = within(tree).getByRole("button", {
      name: /orchestrator\.main — Coordinate the review/,
    })
    // The row's own element, not the nested <li>: the children are rows too.
    const mainRow = mainButton.parentElement
    if (!mainRow) throw new Error("the main run is not in a row")
    expect(within(mainRow).getByText("Failed")).toBeDefined()
    expect(within(mainRow).getByText("previous attempt")).toBeDefined()

    // The note that reads the failure as the session's outcome is gone: the
    // session has not failed, it is waiting to run again.
    const detail = await screen.findByRole("region", { name: "Run details" })
    expect(
      await within(detail).findByText(/the retry reopens this run/),
    ).toBeDefined()
    expect(within(detail).queryByText(/ends the session as failed/)).toBeNull()

    // And the header says what the queue is doing with the session.
    const waiting = screen.getByText(/Waiting for a worker/)
    expect(waiting.textContent).toMatch(/queued 4m ago/)
    expect(waiting.textContent).toMatch(/check that a worker is running/)
  })

  it("leaves a live session's runs as the current attempt", async () => {
    mockApi(LIVE_TREE, { status: "running" })

    render(<SessionDetail sessionId={SESSION_ID} onBack={vi.fn()} />)

    const tree = await screen.findByRole("region", { name: "Agent run tree" })
    const row = within(tree)
      .getByRole("button", { name: /logic-reviewer — Review control flow/ })
      .closest("li")
    if (!row) throw new Error("the running sub-agent is not in a row")
    expect(within(row).getByText("Running")).toBeDefined()

    expect(within(tree).queryByText(/previous attempt/i)).toBeNull()
    expect(screen.queryByText(/previous attempt/i)).toBeNull()
    expect(screen.queryByText(/Waiting for a worker/)).toBeNull()
  })

  it("puts a quiet placeholder where a session has no runs yet", async () => {
    mockApi([])

    render(<SessionDetail sessionId={SESSION_ID} onBack={vi.fn()} />)

    // Radix activates a tab on pointer down, not on the synthetic click.
    fireEvent.mouseDown(await screen.findByRole("tab", { name: "Run tree" }))

    expect(
      await screen.findByText(/No agent runs yet/),
    ).toBeDefined()
    expect(screen.getByRole("region", { name: "Agent run tree" })).toBeDefined()
  })
})
