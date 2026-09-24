import { cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { api } from "@/api/client"
import type * as client from "@/api/client"
import type { Finding, ReviewSession, SessionTarget } from "@/api/contract"
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
const SESSION_ID = "ses_findings"

const POSTED_URL =
  "https://github.com/acme/api-gateway/pull/142#discussion_r2400000042"

const SUGGESTION =
  "if not hmac.compare_digest(signature, expected):\n    raise SignatureError(\"signature mismatch\")"

/**
 * The hunk GitHub printed with the posted comment. Its `+` line is the
 * suggestion's first line and lands on new line 84 — the finding's own line.
 */
const DIFF_HUNK = [
  "@@ -82,4 +82,5 @@ def verify(self, request: Request) -> None:",
  "     expected = self._sign(request.body)",
  '     signature = request.headers.get("X-Hub-Signature-256")',
  `+${SUGGESTION.split("\n")[0]}`,
  '         raise SignatureError("signature mismatch")',
  "     return True",
].join("\n")

const COMMENTED_LINE = SUGGESTION.split("\n")[0]

const FINDINGS: Finding[] = [
  {
    path: "src/auth/tokens.py",
    line: 84,
    severity: "error",
    category: "security",
    message:
      "The webhook body is parsed before the `signature` is checked, so a forged delivery can requeue a target.",
    suggestion: SUGGESTION,
    commentUrl: POSTED_URL,
    author: "slopolis-dev[bot]",
    postedAt: "2026-09-20T09:14:00.000Z",
    diffHunk: DIFF_HUNK,
  },
  {
    path: "src/queue/worker.py",
    line: null,
    severity: "info",
    category: "maintainability",
    message:
      "This branch duplicates the retry rules the queue already owns.",
    suggestion: null,
    commentUrl: null,
    author: null,
    postedAt: null,
    diffHunk: null,
  },
]

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
  findingsCount: FINDINGS.length,
  findings: FINDINGS,
  tokens: 18_000,
  costUsd: 0.08,
}

function session(): ReviewSession {
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
    findingsCount: FINDINGS.length,
  }
}

function mockApi() {
  vi.mocked(api.getSession).mockResolvedValue(session())
  // No runs, so the summary tab — the one that holds the targets — leads.
  vi.mocked(api.getRunTree).mockResolvedValue({ runs: [] })
  vi.mocked(api.getRunEvents).mockResolvedValue({ items: [], nextSeq: null })
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

describe("SessionDetail findings", () => {
  it("renders a target's findings as comment cards, linking only the ones posted inline", async () => {
    mockApi()

    render(<SessionDetail sessionId={SESSION_ID} onBack={vi.fn()} />)

    // The findings are what a target is opened for, so the disclosure starts
    // expanded — and stays collapsible.
    const toggle = await screen.findByRole("button", { name: "2 findings" })
    expect(toggle.getAttribute("aria-expanded")).toBe("true")
    expect(screen.getByText("src/auth/tokens.py:84")).toBeDefined()

    fireEvent.click(toggle)

    expect(toggle.getAttribute("aria-expanded")).toBe("false")
    expect(screen.queryByText("src/auth/tokens.py:84")).toBeNull()

    fireEvent.click(toggle)

    expect(toggle.getAttribute("aria-expanded")).toBe("true")

    // Each finding is a comment card, headed by the file it cites.
    const posted = screen.getByText("src/auth/tokens.py:84").closest("li")
    if (!posted) throw new Error("the posted finding is not in a row")
    const unposted = screen.getByText("src/queue/worker.py").closest("li")
    if (!unposted) throw new Error("the unposted finding is not in a row")

    // The hunk GitHub printed with the comment, numbered from its header: the
    // row the comment sits on is the added one, on new line 84, and it is the
    // row carrying the accent.
    expect(within(posted).getByText(/^@@ -82,4 \+82,5 @@/)).toBeDefined()
    const commented = within(posted).getByText(COMMENTED_LINE).closest("div")
    if (!commented) throw new Error("the commented line is not in a row")
    expect(within(commented).getByText("84")).toBeDefined()
    expect(commented.className).toContain("border-l-accent")
    // The hunk's remaining rows continue *below* the comment, which is where
    // GitHub prints them — the comment is a row inside the diff, not a block
    // after the whole hunk.
    const tail = within(posted).getByText(/return True/)
    const authorLine = within(posted).getByText("slopolis-dev")
    expect(
      authorLine.compareDocumentPosition(tail) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).not.toBe(0)
    // The finding that never posted shows no code block at all.
    expect(within(unposted).queryByText(/^@@/)).toBeNull()

    // The message is the comment body, with its backticked identifier as code.
    expect(within(posted).getByText(/parsed before the/)).toBeDefined()
    expect(within(posted).getByText("signature").tagName).toBe("CODE")
    expect(within(unposted).getByText(FINDINGS[1].message)).toBeDefined()

    // The posted finding reads as a comment: it names the login it is posted
    // as, without GitHub's `[bot]` suffix, and says when it was written.
    const author = within(posted).getByText("slopolis-dev")
    expect(author.getAttribute("title")).toBe("slopolis-dev[bot]")
    expect(within(posted).getByText("bot")).toBeDefined()
    expect(within(posted).getByText(/^commented /)).toBeDefined()

    const link = within(posted).getByRole("link", { name: /View comment/ })
    expect(link.getAttribute("href")).toBe(POSTED_URL)
    expect(link.getAttribute("target")).toBe("_blank")

    // The suggestion is literal replacement code, so it renders as such.
    const suggestion = within(posted).getByText(/hmac\.compare_digest/, {
      selector: "pre",
    })
    expect(suggestion.textContent).toBe(SUGGESTION)

    // The finding with no diff line went into the summary comment, and the row
    // says so instead of wearing a comment header it never earned.
    expect(within(unposted).getByText("Not posted inline")).toBeDefined()
    expect(within(unposted).queryByText(/^commented /)).toBeNull()
    expect(within(unposted).queryByText("bot")).toBeNull()
    expect(within(unposted).queryByRole("link")).toBeNull()
    expect(within(unposted).queryByText(/View comment/)).toBeNull()
  })
})
