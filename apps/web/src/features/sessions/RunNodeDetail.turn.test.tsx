import { cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { api } from "@/api/client"
import type * as client from "@/api/client"
import type { AgentEventItem, AgentEventPage, AgentRunNode } from "@/api/contract"
import { RunNodeDetail } from "./components/RunNodeDetail"

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

const SESSION_ID = "ses_1"

const RUN: AgentRunNode = {
  id: "run_sub",
  sessionId: SESSION_ID,
  targetId: "tgt_1",
  parentRunId: "run_pr",
  level: "sub",
  role: "logic-reviewer",
  modelId: "claude-sonnet-4",
  objective: "Review control flow and edge cases in #142",
  status: "done",
  tokens: 4_800,
  costUsd: 0.02,
  startedAt: "2026-09-20T09:00:05.000Z",
  endedAt: "2026-09-20T09:00:20.000Z",
  error: null,
  children: [],
}

const SYSTEM_CONTENT =
  "You review one pull request for correctness, security, and missing tests."
const USER_CONTENT = [
  "Review acme/api-gateway#142: fix: guard token refresh",
  "",
  "```diff",
  "-  const next = await fetchToken(token)",
  "+  const next = await fetchToken(token, { skewSeconds: 30 })",
  "```",
].join("\n")
const RESPONSE_CONTENT = '{\n  "findings": []\n}'

const STEP_EVENT: AgentEventItem = {
  id: "e_step",
  runId: RUN.id,
  parentRunId: "run_pr",
  seq: 1,
  type: "agent.step",
  payload: {
    step: 1,
    model_id: "claude-sonnet-4",
    summary: "Reading the changed files for acme/api-gateway#142.",
    tool_call_count: 1,
  },
  createdAt: "2026-09-20T09:00:06.000Z",
}

const TURN_EVENT: AgentEventItem = {
  id: "e_turn",
  runId: RUN.id,
  parentRunId: "run_pr",
  seq: 2,
  type: "agent.turn",
  payload: {
    model_id: "claude-sonnet-4",
    messages: [
      { role: "system", content: SYSTEM_CONTENT },
      { role: "user", content: USER_CONTENT },
    ],
    response: RESPONSE_CONTENT,
    prompt_tokens: 1_900,
    completion_tokens: 120,
    total_tokens: 2_020,
    cost_usd: 0.01,
    chars: 3_100,
    truncated: false,
  },
  createdAt: "2026-09-20T09:00:10.000Z",
}

/** A payload this build cannot read: `messages` is not an array at all. */
const MALFORMED_TURN_EVENT: AgentEventItem = {
  id: "e_turn_bad",
  runId: RUN.id,
  parentRunId: "run_pr",
  seq: 3,
  type: "agent.turn",
  payload: {
    model_id: "claude-sonnet-4",
    messages: "the model transcript",
    total_tokens: 900,
    cost_usd: 0.004,
  },
  createdAt: "2026-09-20T09:00:11.000Z",
}

/** A turn whose stored copy lost text at the server's character cap. */
const TRUNCATED_TURN_EVENT: AgentEventItem = {
  ...TURN_EVENT,
  id: "e_turn_clipped",
  seq: 4,
  payload: { ...TURN_EVENT.payload, truncated: true },
  createdAt: "2026-09-20T09:00:12.000Z",
}

function page(items: AgentEventItem[]): AgentEventPage {
  return { items, nextSeq: null }
}

function renderRun(events: AgentEventItem[]) {
  vi.mocked(api.getRunEvents).mockResolvedValue(page(events))
  render(
    <RunNodeDetail
      sessionId={SESSION_ID}
      run={RUN}
      sessionStatus="done"
      liveEvents={[]}
    />,
  )
}

afterEach(() => {
  cleanup()
})

describe("RunNodeDetail model turns", () => {
  it("reveals an agent.turn's prompt and response behind a collapsed toggle", async () => {
    renderRun([STEP_EVENT, TURN_EVENT])

    const detail = await screen.findByRole("region", { name: "Run details" })
    // The row reads as a one-line digest until the prompt is asked for.
    expect(await within(detail).findByText("Model turn")).toBeDefined()
    expect(within(detail).getByText(/2 messages/)).toBeDefined()
    const toggle = within(detail).getByRole("button", { name: "Show prompt" })
    expect(within(detail).queryByText(SYSTEM_CONTENT)).toBeNull()

    fireEvent.click(toggle)

    expect(within(detail).getByText(SYSTEM_CONTENT)).toBeDefined()
    // Multi-line bodies: locate the node, then assert its exact text.
    expect(
      within(detail).getByText(/Review acme\/api-gateway#142/).textContent,
    ).toBe(USER_CONTENT)
    expect(within(detail).getByText("system")).toBeDefined()
    expect(within(detail).getByText("user")).toBeDefined()
    expect(within(detail).getByText("Response")).toBeDefined()
    expect(
      within(detail).getByText(/"findings"/).textContent,
    ).toBe(RESPONSE_CONTENT)

    // And the toggle reads the other way once the body is open.
    fireEvent.click(within(detail).getByRole("button", { name: "Hide prompt" }))
    expect(within(detail).queryByText(SYSTEM_CONTENT)).toBeNull()
  })

  it("keeps a row readable when an agent.turn payload cannot be parsed", async () => {
    renderRun([STEP_EVENT, MALFORMED_TURN_EVENT])

    const detail = await screen.findByRole("region", { name: "Run details" })
    // The turn still names itself and keeps a scalar digest, and the run's
    // other events render beside it — an unreadable payload is not a crash.
    expect(await within(detail).findByText("Model turn")).toBeDefined()
    expect(within(detail).getByText(/900 tokens/)).toBeDefined()
    expect(within(detail).getByText(/Reading the changed files/)).toBeDefined()
    expect(within(detail).queryByRole("button", { name: /prompt/i })).toBeNull()
    expect(within(detail).queryByText(/the model transcript/)).toBeNull()
  })

  it("says the stored copy was clipped when a turn came back truncated", async () => {
    renderRun([TRUNCATED_TURN_EVENT])

    const detail = await screen.findByRole("region", { name: "Run details" })
    fireEvent.click(
      await within(detail).findByRole("button", { name: "Show prompt" }),
    )

    expect(
      within(detail).getByText(/clipped at the 200,000-character cap/),
    ).toBeDefined()
  })
})
