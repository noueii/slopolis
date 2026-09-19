import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { ChevronUp, GitPullRequest } from "lucide-react"

import { cn } from "@/lib/utils"
import type { MockScenario } from "@/api/client"
import type { OpenPullRequest } from "@/api/contract"
import { Button } from "@/components/ui/button"
import { AnalyticsStrip } from "./AnalyticsStrip"
import {
  DashboardError,
  DashboardEmpty,
  DashboardSkeleton,
} from "./DashboardStates"
import {
  NewReviewComposer,
  type NewReviewComposerHandle,
} from "./NewReviewComposer"
import { ReviewList } from "./ReviewList"
import { PullRequestPicker } from "./components/PullRequestPicker"
import {
  clearPullRequestCache,
  useDashboard,
  usePresets,
  useRepositories,
} from "./lib/useDashboard"
import {
  selectionKey,
  toSelectedPr,
  type SelectedPr,
} from "./lib/selection"
import { useStickyCollapse } from "./lib/useStickyCollapse"

export interface DashboardScreenProps {
  scenario: MockScenario
  onOpenSession: (id: string) => void
  focusComposerNonce?: number
}

export function DashboardScreen({
  scenario,
  onOpenSession,
  focusComposerNonce = 0,
}: DashboardScreenProps) {
  const [selected, setSelected] = useState<SelectedPr[]>([])
  const [prompt, setPrompt] = useState("")
  const [preset, setPreset] = useState("default")
  const [pickerOpen, setPickerOpen] = useState(false)

  const rootRef = useRef<HTMLDivElement>(null)
  const composerWrapRef = useRef<HTMLDivElement>(null)
  const composerHandleRef = useRef<NewReviewComposerHandle>(null)

  const repositories = useRepositories()
  const dashboard = useDashboard({ limit: 12 })
  const presets = usePresets()
  const { refetch: refetchDashboard } = dashboard
  const { refetch: refetchRepositories } = repositories
  const { refetch: refetchPresets } = presets

  const { collapsed, expand } = useStickyCollapse(rootRef, composerWrapRef)

  const scenarioInitialised = useRef(false)
  useEffect(() => {
    if (!scenarioInitialised.current) {
      scenarioInitialised.current = true
      return
    }
    clearPullRequestCache()
    refetchDashboard()
    refetchRepositories()
    refetchPresets()
  }, [scenario, refetchDashboard, refetchRepositories, refetchPresets])

  const addPr = useCallback((pr: SelectedPr) => {
    setSelected((prev) =>
      prev.some((item) => item.url === pr.url) ? prev : [...prev, pr],
    )
  }, [])

  const removePr = useCallback((url: string) => {
    setSelected((prev) => prev.filter((item) => item.url !== url))
  }, [])

  const clearSelected = useCallback(() => setSelected([]), [])

  const togglePr = useCallback((pr: OpenPullRequest) => {
    setSelected((prev) => {
      const key = selectionKey(pr)
      return prev.some((item) => selectionKey(item) === key)
        ? prev.filter((item) => selectionKey(item) !== key)
        : [...prev, toSelectedPr(pr)]
    })
  }, [])

  const focusComposer = useCallback(() => {
    expand()
    const wrap = composerWrapRef.current
    if (wrap && typeof wrap.scrollIntoView === "function") {
      wrap.scrollIntoView({ behavior: "smooth", block: "start" })
    }
    window.setTimeout(() => composerHandleRef.current?.focus(), 320)
  }, [expand])

  useEffect(() => {
    if (focusComposerNonce <= 0) return
    focusComposer()
  }, [focusComposerNonce, focusComposer])

  const presetLabel = useMemo(() => {
    const match = presets.data?.presets.find((item) => item.id === preset)
    return match?.name ?? "Default"
  }, [preset, presets.data])

  const summary = dashboard.data?.summary
  const showSkeleton = dashboard.status === "loading" && !dashboard.data
  const showError = dashboard.status === "error"
  const showContent = !showError && dashboard.data !== null

  return (
    <div ref={rootRef} className="relative">
      {collapsed ? (
        <CollapsedComposerBar
          count={selected.length}
          prompt={prompt}
          presetLabel={presetLabel}
          onExpand={expand}
        />
      ) : null}

      <div className="mx-auto flex w-full max-w-[1180px] flex-col gap-6 p-6">
        <AnalyticsStrip
          summary={summary}
          generatedAt={dashboard.data?.generatedAt}
        />

        <section className="flex flex-col justify-center gap-6 md:min-h-[calc(100dvh-16rem)]">
          <div ref={composerWrapRef} className="mx-auto w-full max-w-3xl">
            <NewReviewComposer
              ref={composerHandleRef}
              selected={selected}
              prompt={prompt}
              preset={preset}
              presetCatalog={presets.data}
              presetStatus={presets.status}
              onPresetChange={setPreset}
              onRetryPresets={refetchPresets}
              onPromptChange={setPrompt}
              onRemove={removePr}
              onAdd={addPr}
              onClear={clearSelected}
              onOpenPicker={() => setPickerOpen(true)}
              onOpenSession={onOpenSession}
            />
          </div>

          {showSkeleton ? <DashboardSkeleton /> : null}

          {showError ? (
            <DashboardError
              message={
                dashboard.error ??
                "Something went wrong while loading the dashboard."
              }
              onRetry={refetchDashboard}
            />
          ) : null}

          {showContent && dashboard.data ? (
            dashboard.data.running.length === 0 &&
            dashboard.data.recent.length === 0 ? (
              <DashboardEmpty scoped={false} onNewReview={focusComposer} />
            ) : (
              <ReviewList
                running={dashboard.data.running}
                recent={dashboard.data.recent}
                onOpenSession={onOpenSession}
              />
            )
          ) : null}
        </section>
      </div>

      <PullRequestPicker
        open={pickerOpen}
        onOpenChange={setPickerOpen}
        repositories={repositories.data?.items ?? null}
        repositoriesLoading={repositories.status === "loading"}
        repositoriesError={repositories.error}
        onRetryRepositories={refetchRepositories}
        selected={selected}
        onToggle={togglePr}
        onClear={clearSelected}
      />
    </div>
  )
}

function CollapsedComposerBar({
  count,
  prompt,
  presetLabel,
  onExpand,
}: {
  count: number
  prompt: string
  presetLabel: string
  onExpand: () => void
}) {
  const preview =
    prompt.trim() ||
    (count > 0
      ? "No focus prompt — built-in review only."
      : "Select pull requests to begin.")

  return (
    <div className="sticky top-0 z-30 h-0">
      <div className="animate-fade-in border-b border-border bg-background/85 backdrop-blur supports-[backdrop-filter]:bg-background/70">
        <div className="mx-auto flex w-full max-w-[1180px] items-center gap-3 px-4 py-2.5 lg:px-6">
          <span className="grid size-7 shrink-0 place-items-center rounded-md bg-accent/10 text-accent">
            <GitPullRequest className="size-3.5" />
          </span>
          <span
            className={cn(
              "shrink-0 rounded-full border px-2 py-0.5 font-mono text-[11px] font-semibold",
              count > 0
                ? "border-accent/30 bg-accent/10 text-accent"
                : "border-border bg-muted/50 text-muted-foreground",
            )}
          >
            {count} PR{count === 1 ? "" : "s"}
          </span>
          <span className="hidden shrink-0 items-center gap-1.5 rounded-full border border-border bg-muted/40 px-2 py-0.5 font-mono text-[11px] text-muted-foreground md:inline-flex">
            <span className="size-1.5 rounded-full bg-accent" />
            {presetLabel}
          </span>
          <span className="min-w-0 flex-1 truncate text-[13px] text-muted-foreground">
            {preview}
          </span>
          <Button
            variant="outline"
            size="sm"
            className="shrink-0"
            onClick={onExpand}
          >
            <ChevronUp data-icon="inline-start" />
            <span className="hidden sm:inline">Expand</span>
          </Button>
        </div>
      </div>
    </div>
  )
}
