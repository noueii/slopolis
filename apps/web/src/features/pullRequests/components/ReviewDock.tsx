/**
 * The review dock (spec v3 §4): a bottom-docked panel holding the selected
 * pull requests, the preset, and the review focus prompt.
 *
 * It is the only way a review starts — the inbox row click selects, and the
 * selection raises this. The dock stays mounted while a session it just created
 * is on screen, so clearing the selection cannot take the confirmation with it.
 * The sticky positioning is `bottom-0` on the last flow element of the screen,
 * so mid-list rows scroll behind it and nothing is clipped at the end.
 */

import { useState } from "react"

import type { ReviewPresetCatalog } from "@/api/contract"
import {
  NewReviewComposer,
  type NewReviewComposerProps,
} from "../NewReviewComposer"
import type { SelectedPr } from "../lib/selection"

export interface ReviewDockProps {
  selected: SelectedPr[]
  prompt: string
  preset: string
  presetCatalog: ReviewPresetCatalog | null
  presetStatus: NewReviewComposerProps["presetStatus"]
  onPresetChange: (value: string) => void
  onRetryPresets?: () => void
  onPromptChange: (value: string) => void
  onRemove: (url: string) => void
  onClear: () => void
  onOpenSession?: (id: string) => void
}

export function ReviewDock({
  selected,
  prompt,
  preset,
  presetCatalog,
  presetStatus,
  onPresetChange,
  onRetryPresets,
  onPromptChange,
  onRemove,
  onClear,
  onOpenSession,
}: ReviewDockProps) {
  // A created session outlives the selection it was submitted from.
  const [created, setCreated] = useState(false)

  if (selected.length === 0 && !created) return null

  return (
    <div className="sticky bottom-0 z-30 -mx-6 mt-6 pb-[env(safe-area-inset-bottom)]">
      <div className="pointer-events-none absolute inset-x-0 bottom-0 h-28 bg-gradient-to-t from-background via-background/85 to-transparent" />
      <div className="relative px-6 pb-4">
        <NewReviewComposer
          selected={selected}
          prompt={prompt}
          preset={preset}
          presetCatalog={presetCatalog}
          presetStatus={presetStatus}
          onPresetChange={onPresetChange}
          onRetryPresets={onRetryPresets}
          onPromptChange={onPromptChange}
          onRemove={onRemove}
          onClear={onClear}
          onOpenSession={onOpenSession}
          onCreated={() => setCreated(true)}
          onReset={() => setCreated(false)}
        />
      </div>
    </div>
  )
}
