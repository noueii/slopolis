/**
 * The composer's refusal path.
 *
 * Pre-flight can reject every link for reasons that have nothing to do with
 * coverage (access, a parked repository, the live model check). The composer
 * used to assert a coverage problem and then hide pre-flight's own notices, so
 * the user was told the one thing that was not true and never saw the line that
 * said what to fix.
 */

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { api } from "@/api/client"
import { TooltipProvider } from "@/components/ui/tooltip"
import type { PrReference, ReviewPresetCatalog } from "@/api/contract"
import { NewReviewComposer } from "./NewReviewComposer"
import type { SelectedPr } from "./lib/selection"

vi.mock("@/api/client", () => ({
  api: { preflightReview: vi.fn(), createReviewSession: vi.fn() },
  ApiError: class ApiError extends Error {},
}))

const selected: SelectedPr[] = [
  {
    url: "https://github.com/noueii/cstm/pull/3",
    repository: {
      id: "repo_1",
      fullName: "noueii/cstm",
      private: true,
      defaultBranch: "main",
    },
    number: 3,
    title: "feat: decrement the author's post count",
  } satisfies PrReference,
]

const presets: ReviewPresetCatalog = {
  defaultPresetId: "default",
  presets: [{ id: "default", name: "Default", description: "Balanced." }],
}

function renderComposer() {
  // The shell owns the tooltip provider in the app; the composer assumes it.
  return render(
    <TooltipProvider>
      <NewReviewComposer
        selected={selected}
        prompt=""
        onPromptChange={vi.fn()}
        onRemove={vi.fn()}
        onClear={vi.fn()}
        preset="default"
        onPresetChange={vi.fn()}
        presetCatalog={presets}
        presetStatus="success"
      />
    </TooltipProvider>,
  )
}

afterEach(cleanup)

describe("pre-flight refusals", () => {
  it("shows pre-flight's own reason instead of claiming a coverage problem", async () => {
    vi.mocked(api.preflightReview).mockResolvedValue({
      valid: [],
      invalid: [selected[0].url],
      notices: [
        "Live model check failed for claude-sonnet-4: the model gateway is not configured, so no review can call a model; set LITELLM_BASE_URL and LITELLM_MASTER_KEY on the server and restart.",
      ],
    })

    renderComposer()
    fireEvent.click(screen.getByLabelText("Start review"))

    expect(await screen.findByText(/LITELLM_MASTER_KEY/)).toBeDefined()
    expect(screen.queryByText(/covered by this workspace/i)).toBeNull()
    expect(api.createReviewSession).not.toHaveBeenCalled()
  })

  it("still says something when pre-flight explains nothing", async () => {
    vi.mocked(api.preflightReview).mockResolvedValue({
      valid: [],
      invalid: [selected[0].url],
      notices: [],
    })

    renderComposer()
    fireEvent.click(screen.getByLabelText("Start review"))

    await waitFor(() =>
      expect(screen.getByText(/No pull request passed pre-flight/i)).toBeDefined(),
    )
    expect(api.createReviewSession).not.toHaveBeenCalled()
  })

  it("submits the links that did validate", async () => {
    vi.mocked(api.preflightReview).mockResolvedValue({
      valid: selected,
      invalid: [],
      notices: [],
    })
    vi.mocked(api.createReviewSession).mockResolvedValue({
      id: "sess_1",
      title: "Decrement the author's post count",
      name: "noueii/cstm#3",
      status: "queued",
      model: "claude-sonnet-4",
      provider: "Anthropic",
      targetCount: 1,
      createdAt: "2026-01-01T00:00:00.000Z",
    })

    renderComposer()
    fireEvent.click(screen.getByLabelText("Start review"))

    await waitFor(() =>
      expect(api.createReviewSession).toHaveBeenCalledWith(
        expect.objectContaining({ prUrls: [selected[0].url] }),
      ),
    )
  })
})
