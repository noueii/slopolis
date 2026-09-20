/**
 * The mock session stream: the status projection it always sent, plus the live
 * harness frames a running session now carries (spec v2 §7).
 *
 * The frames exist to make the run-tree panel animate, so what these tests hold
 * them to is that agreement: a client that folds the frames onto the tree it
 * read must end up exactly where the tree endpoint lands once the worker (the
 * stream itself, in the mock) has moved on.
 */

import { setupServer } from "msw/node"
import { afterAll, beforeAll, describe, expect, it } from "vitest"

import type {
  AgentEventItem,
  AgentRunNode,
  AgentRunTreeResponse,
  ReviewSession,
} from "@/api/contract"
import {
  findRun,
  flattenRuns,
  isTerminalRunStatus,
  mergeAgentEvent,
} from "@/features/sessions/lib/runTree"
import { RETRY_SESSION_ID } from "./data"
import { dataset } from "./dataset"
import { runsHandlers } from "./runs"
import { sessionsHandlers } from "./sessions"

const server = setupServer(...sessionsHandlers, ...runsHandlers)

beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterAll(() => server.close())

/** A live stream paces itself, so these reads wait real time. */
const STREAM_TIMEOUT_MS = 30_000

interface SseFrame {
  event: string
  data: string
}

interface Snapshot {
  id: string
  status: string
  targets: Array<{ id: string; status: string }>
}

function parseFrames(body: string): SseFrame[] {
  return body
    .split("\n\n")
    .filter((block) => block.trim().length > 0)
    .map((block) => {
      const lines = block.split("\n")
      return {
        event: lines[0].replace(/^event: /, ""),
        data: lines[1].replace(/^data: /, ""),
      }
    })
}

/** Read a session's stream to its close, one frame per blank-line block. */
async function readStream(sessionId: string): Promise<SseFrame[]> {
  const response = await fetch(`/api/sessions/${sessionId}/events`)
  return parseFrames(await response.text())
}

async function readTree(sessionId: string): Promise<AgentRunNode[]> {
  const response = await fetch(`/api/sessions/${sessionId}/runs/tree`)
  const body = (await response.json()) as AgentRunTreeResponse
  return body.runs
}

function agentEvents(frames: SseFrame[]): AgentEventItem[] {
  return frames
    .filter((frame) => frame.event === "agent")
    .map((frame) => JSON.parse(frame.data) as AgentEventItem)
}

/** The newest frame of one kind, or `undefined` when the stream sent none. */
function lastFrameOf(frames: SseFrame[], event: string): SseFrame | undefined {
  for (let index = frames.length - 1; index >= 0; index -= 1) {
    if (frames[index].event === event) return frames[index]
  }
  return undefined
}

/** The last status projection the stream sent, which `done` repeats. */
function lastSnapshot(frames: SseFrame[]): Snapshot {
  const closing = lastFrameOf(frames, "session")
  if (!closing) throw new Error("the stream carried no session snapshot")
  return JSON.parse(closing.data) as Snapshot
}

/** A running session in the shape a scenario needs, or a failed test. */
function runningSession(
  shape: (session: ReviewSession) => boolean,
): ReviewSession {
  const found = dataset.sessions.find(
    (session) => session.status === "running" && shape(session),
  )
  if (!found) throw new Error("the dataset has no running session of that shape")
  return found
}

/** Fold delivered frames onto a tree the way the run-tree hook does. */
function foldFrames(
  runs: AgentRunNode[],
  events: AgentEventItem[],
  sessionId: string,
): AgentRunNode[] {
  return events.reduce(
    (folded, event) => mergeAgentEvent(folded, event, sessionId),
    runs,
  )
}

describe("mock session stream", () => {
  it("streams live frames that fold onto exactly the tree it serves", async () => {
    const session = runningSession(() => true)
    const before = await readTree(session.id)
    const frames = await readStream(session.id)
    const events = agentEvents(frames)

    expect(frames[0]?.event).toBe("session")
    expect(frames[frames.length - 1]?.event).toBe("done")
    // The close repeats the final snapshot, as it always has.
    expect(frames[frames.length - 1]?.data).toBe(
      lastFrameOf(frames, "session")?.data,
    )
    expect(events.length).toBeGreaterThan(0)

    const after = await readTree(session.id)
    expect(after).not.toEqual(before)

    // Every frame belongs to a run the session's tree holds, and no run's
    // sequence numbers go backwards across the stream.
    const seqs = new Map<string, number>()
    for (const event of events) {
      expect(findRun(after, event.runId)).not.toBeNull()
      expect(event.seq).toBeGreaterThan(seqs.get(event.runId) ?? 0)
      seqs.set(event.runId, event.seq)
    }

    // A completion the stream reported is a completion the tree holds.
    const completions = events.filter((event) => event.type === "agent.completed")
    expect(completions.length).toBeGreaterThan(0)
    for (const event of completions) {
      const node = findRun(after, event.runId)
      expect(node?.status).toBe("done")
      expect(node?.endedAt).toBe(event.createdAt)
    }

    // The fold the panel applies lands on the tree a re-read returns.
    expect(foldFrames(before, events, session.id)).toEqual(after)
  }, STREAM_TIMEOUT_MS)

  it("completes a sub-agent the tree showed as running", async () => {
    const session = runningSession((candidate) =>
      candidate.targets.some((target) => target.status === "running"),
    )
    const before = await readTree(session.id)
    const working = flattenRuns(before).filter(
      ({ node }) => node.level === "sub" && node.status === "running",
    )
    expect(working.length).toBeGreaterThan(0)

    const events = agentEvents(await readStream(session.id))
    const watched = working[0].node
    const completion = events.find(
      (event) => event.runId === watched.id && event.type === "agent.completed",
    )
    expect(completion).toBeDefined()

    const folded = foldFrames(before, events, session.id)
    const ended = findRun(folded, watched.id)
    expect(ended?.status).toBe("done")
    expect(ended?.endedAt).toBe(completion?.createdAt)
    expect(findRun(await readTree(session.id), watched.id)).toEqual(ended)
  }, STREAM_TIMEOUT_MS)

  it("starts a queued target's orchestrator and reports that target running", async () => {
    const session = runningSession((candidate) =>
      candidate.targets.some((target) => target.status === "queued"),
    )
    const target = session.targets.find((row) => row.status === "queued")
    if (!target) throw new Error("the session lost its queued target")

    const before = await readTree(session.id)
    expect(
      flattenRuns(before).some(({ node }) => node.targetId === target.id),
    ).toBe(false)

    const frames = await readStream(session.id)
    const after = await readTree(session.id)

    const orchestrator = flattenRuns(after).find(
      ({ node }) => node.level === "pr" && node.targetId === target.id,
    )
    expect(orchestrator?.node.status).toBe("running")
    const reviewers = flattenRuns(after).filter(
      ({ node }) => node.parentRunId === orchestrator?.node.id,
    )
    expect(reviewers.map(({ node }) => node.status)).toEqual(["done"])

    // The streamed projection has to agree with the tree it grew, or the target
    // row would read `queued` beside its running orchestrator — and a re-read of
    // the session has to keep saying so.
    expect(
      lastSnapshot(frames).targets.find((row) => row.id === target.id)?.status,
    ).toBe("running")
    const detail = (await (
      await fetch(`/api/sessions/${session.id}`)
    ).json()) as ReviewSession
    expect(
      detail.targets.find((row) => row.id === target.id)?.status,
    ).toBe("running")
    expect(foldFrames(before, agentEvents(frames), session.id)).toEqual(after)
  }, STREAM_TIMEOUT_MS)

  it("seeds a requeued session whose tree is the superseded attempt", async () => {
    const retried = dataset.sessions.find(
      (session) => session.id === RETRY_SESSION_ID,
    )
    if (!retried) throw new Error("the dataset lost its retried session")

    // The rows went back to `queued`; nothing about them is running again yet.
    expect(retried.status).toBe("queued")
    expect(retried.targets.every((target) => target.status === "queued")).toBe(
      true,
    )

    // The tree, though, is the attempt that already failed — the state the run
    // tree panel has to explain instead of showing a blank pending session.
    const runs = flattenRuns(await readTree(retried.id))
    expect(runs.length).toBeGreaterThan(0)
    expect(runs.every(({ node }) => isTerminalRunStatus(node.status))).toBe(true)
    const main = runs.find(({ node }) => node.level === "main")
    expect(main?.node.status).toBe("failed")
    expect(
      runs.some(
        ({ node }) => node.level === "pr" && node.status === "failed",
      ),
    ).toBe(true)

    // A queued session has no worker streaming for it, so the stream is one
    // snapshot and a close.
    const frames = await readStream(retried.id)
    expect(frames.map((frame) => frame.event)).toEqual(["session", "done"])
    expect(lastSnapshot(frames).status).toBe("queued")
  })

  it("sends a settled session a snapshot and a close, and nothing else", async () => {
    const settled = dataset.sessions.find((session) => session.status === "done")
    if (!settled) throw new Error("the dataset has no settled session")

    const frames = await readStream(settled.id)

    expect(frames.map((frame) => frame.event)).toEqual(["session", "done"])
    expect(lastSnapshot(frames).status).toBe("done")
  })
})
