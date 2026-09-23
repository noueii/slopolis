/**
 * The pull-request inbox (spec v3 §1–§5): every open pull request across the
 * workspace's connected repositories, the filters that narrow them, and the
 * dock that turns a selection into a review.
 *
 * The screen owns the request (params + debounced search), the selection, and
 * the dock's prompt/preset. Rows and the dock never hold selection state of
 * their own, so a selection survives a filter change.
 */

import { useEffect, useMemo, useRef, useState } from "react"
import { RefreshCw } from "lucide-react"

import { cn } from "@/lib/utils"
import { navigate } from "@/lib/route"
import type { MockScenario } from "@/api/client"
import type { PullRequestListItem, PullRequestListParams } from "@/api/contract"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { formatCount } from "@/features/sessions/lib/format"
import { useDebouncedValue } from "@/features/sessions/lib/useSessions"
import { SessionsPagination } from "@/features/sessions/SessionsPagination"
import { useRepositories } from "@/features/repositories/lib/useRepositories"
import { PullRequestFilters } from "./components/PullRequestFilters"
import { PullRequestRow } from "./components/PullRequestRow"
import {
  PullRequestEmpty,
  PullRequestError,
  PullRequestSkeleton,
  emptyKindFor,
} from "./components/PullRequestStates"
import { ReviewDock } from "./components/ReviewDock"
import { usePullRequests } from "./lib/usePullRequests"
import { usePresets } from "./lib/usePresets"
import {
  selectionKey,
  toSelectedPr,
  type SelectedPr,
} from "./lib/selection"

export interface PullRequestsScreenProps {
  scenario: MockScenario
  onOpenSession: (id: string) => void
  /** Bumped by the shell's "New review" action; focuses the search field. */
  focusSearchNonce?: number
}

const BASE_PARAMS: PullRequestListParams = {
  page: 1,
  pageSize: 25,
  sort: "updated_desc",
}

const DEFAULT_PRESET = "default"

export function PullRequestsScreen({
  scenario,
  onOpenSession,
  focusSearchNonce = 0,
}: PullRequestsScreenProps) {
  const [params, setParams] = useState<PullRequestListParams>(BASE_PARAMS)
  const [searchInput, setSearchInput] = useState("")
  const debouncedSearch = useDebouncedValue(searchInput, 250)
  const [selection, setSelection] = useState<SelectedPr[]>([])
  const [prompt, setPrompt] = useState("")
  const [preset, setPreset] = useState(DEFAULT_PRESET)

  const searchRef = useRef<HTMLInputElement>(null)
  /** The row a shift-click extends from, kept as a key so it survives paging. */
  const anchorRef = useRef<string | null>(null)

  const { data, status, error, refetch } = usePullRequests(params)
  const presets = usePresets()
  const { refetch: refetchPresets } = presets
  const repositories = useRepositories()
  const { refetch: refetchRepositories } = repositories

  useEffect(() => {
    setParams((prev) => {
      const nextQuery = debouncedSearch.trim() || undefined
      if (prev.q === nextQuery) return prev
      return { ...prev, q: nextQuery, page: 1 }
    })
  }, [debouncedSearch])

  // Re-request when the mock dataset/behaviour changes (skips the first run).
  const scenarioInitialised = useRef(false)
  useEffect(() => {
    if (!scenarioInitialised.current) {
      scenarioInitialised.current = true
      return
    }
    refetch()
    refetchPresets()
    refetchRepositories()
  }, [scenario, refetch, refetchPresets, refetchRepositories])

  useEffect(() => {
    if (focusSearchNonce > 0) searchRef.current?.focus()
  }, [focusSearchNonce])

  const items = useMemo(() => data?.items ?? [], [data])
  const pageKeys = useMemo(() => items.map(selectionKey), [items])
  const selectedKeys = useMemo(
    () => new Set(selection.map(selectionKey)),
    [selection],
  )
  const selectedOnPage = pageKeys.filter((key) => selectedKeys.has(key)).length

  const activeCount = useMemo(
    () =>
      [
        params.q,
        params.repo,
        params.review,
        params.checks,
        params.includeDrafts,
      ].filter(Boolean).length,
    [params],
  )

  const setFilter = (patch: Partial<PullRequestListParams>) =>
    setParams((prev) => ({ ...prev, ...patch, page: 1 }))

  const handleClear = () => {
    setSearchInput("")
    setParams({ page: 1, pageSize: params.pageSize, sort: params.sort })
  }

  const handleRefresh = () => refetch()

  const toggleRow = (
    pr: PullRequestListItem,
    { range }: { range: boolean },
  ) => {
    const key = selectionKey(pr)
    const anchor = anchorRef.current
    const anchorIndex =
      anchor === null
        ? -1
        : items.findIndex((item) => selectionKey(item) === anchor)
    const index = items.findIndex((item) => selectionKey(item) === key)

    if (range && anchorIndex >= 0 && index >= 0 && anchorIndex !== index) {
      const [start, end] =
        anchorIndex < index ? [anchorIndex, index] : [index, anchorIndex]
      const span = items.slice(start, end + 1)
      // The clicked endpoint decides the direction, so a range can be cleared.
      const shouldSelect = !selectedKeys.has(key)
      setSelection((prev) => {
        const next = new Map(prev.map((item) => [selectionKey(item), item]))
        for (const item of span) {
          if (shouldSelect) next.set(selectionKey(item), toSelectedPr(item))
          else next.delete(selectionKey(item))
        }
        return [...next.values()]
      })
      anchorRef.current = key
      return
    }

    setSelection((prev) =>
      prev.some((item) => selectionKey(item) === key)
        ? prev.filter((item) => selectionKey(item) !== key)
        : [...prev, toSelectedPr(pr)],
    )
    anchorRef.current = key
  }

  const selectPage = () =>
    setSelection((prev) => {
      const next = new Map(prev.map((item) => [selectionKey(item), item]))
      for (const item of items) next.set(selectionKey(item), toSelectedPr(item))
      return [...next.values()]
    })

  const clearPage = () =>
    setSelection((prev) =>
      prev.filter((item) => !pageKeys.includes(selectionKey(item))),
    )

  const showSkeleton = status === "loading" && !data
  const showError = status === "error"
  const showRows = !showError && data !== null && items.length > 0
  const showEmpty = !showError && !showSkeleton && data !== null && items.length === 0
  const busy = status === "loading"

  const summary = data?.summary
  const backlog = summary ? (
    <>
      <span className="tabular font-mono text-foreground">
        {formatCount(summary.total)}
      </span>{" "}
      open
      {" · "}
      <span className="tabular font-mono text-foreground">
        {formatCount(summary.needsReview)}
      </span>{" "}
      need review
      {" · "}
      <span
        className={cn(
          "tabular font-mono",
          summary.stale > 0 ? "text-warning" : "text-foreground",
        )}
      >
        {formatCount(summary.stale)}
      </span>{" "}
      stale
      {" · "}
      <span
        className={cn(
          "tabular font-mono",
          summary.running > 0 ? "text-info" : "text-foreground",
        )}
      >
        {formatCount(summary.running)}
      </span>{" "}
      running
    </>
  ) : status === "error" ? (
    "Pull request activity unavailable"
  ) : (
    "Loading pull requests…"
  )

  return (
    <div
      className="mx-auto flex w-full max-w-[1500px] animate-fade-up flex-col gap-4 p-6"
      aria-busy={busy}
    >
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div className="flex flex-col gap-1">
          <h1 className="text-xl font-semibold tracking-tight">Pull requests</h1>
          <p className="text-sm text-muted-foreground">{backlog}</p>
        </div>
        <Button variant="outline" size="sm" onClick={handleRefresh}>
          <RefreshCw
            data-icon="inline-start"
            className={cn(busy && "animate-spin")}
          />
          Refresh
        </Button>
      </header>

      {/* The bar stays put while rows scroll, so a filter is always in reach. */}
      <div className="sticky top-0 z-20 -mx-6 border-b border-border bg-background/95 px-6 py-2.5 backdrop-blur">
        <PullRequestFilters
          filters={data?.filterOptions ?? null}
          params={params}
          searchInput={searchInput}
          onSearchChange={setSearchInput}
          onFilterChange={setFilter}
          onClear={handleClear}
          activeCount={activeCount}
          searchRef={searchRef}
        />
      </div>

      {showSkeleton ? <PullRequestSkeleton /> : null}

      {showError ? (
        <PullRequestError
          message={error ?? "Something went wrong while loading pull requests."}
          onRetry={refetch}
        />
      ) : null}

      {showEmpty ? (
        <PullRequestEmpty
          kind={emptyKindFor(
            data.summary,
            repositories.data?.items.length ?? null,
            activeCount,
          )}
          onClearFilters={handleClear}
          onOpenRepositories={() =>
            navigate({ kind: "nav", id: "repositories" })
          }
        />
      ) : null}

      {showRows ? (
        <div
          className={cn(
            "overflow-hidden rounded-lg border border-border bg-card transition-opacity",
            busy && "pointer-events-none opacity-60",
          )}
        >
          <div className="flex flex-wrap items-center gap-3 border-b border-border bg-muted/30 px-3 py-2">
            <Checkbox
              checked={
                selectedOnPage === 0
                  ? false
                  : selectedOnPage === pageKeys.length
                    ? true
                    : "indeterminate"
              }
              onCheckedChange={(checked) =>
                checked ? selectPage() : clearPage()
              }
              aria-label="Select every pull request on this page"
            />
            <span className="text-2xs font-semibold uppercase tracking-widest text-muted-foreground">
              {items.length} pull request{items.length === 1 ? "" : "s"} on this
              page
            </span>
            {selectedOnPage > 0 ? (
              <Button
                variant="ghost"
                size="sm"
                className="h-7 px-2 text-[13px] text-muted-foreground"
                onClick={clearPage}
              >
                Clear page
              </Button>
            ) : null}
            {selection.length > 0 ? (
              <span className="tabular ml-auto font-mono text-2xs text-muted-foreground">
                {selection.length} selected
              </span>
            ) : null}
          </div>

          <ul className="divide-y divide-border">
            {items.map((pr) => (
              <PullRequestRow
                key={selectionKey(pr)}
                pr={pr}
                selected={selectedKeys.has(selectionKey(pr))}
                onToggle={toggleRow}
                onOpenSession={onOpenSession}
              />
            ))}
          </ul>

          <SessionsPagination
            page={data.page}
            pageSize={data.pageSize}
            total={data.total}
            totalPages={data.totalPages}
            onPageChange={(page) => setParams((prev) => ({ ...prev, page }))}
            onPageSizeChange={(pageSize) =>
              setParams((prev) => ({ ...prev, pageSize, page: 1 }))
            }
          />
        </div>
      ) : null}

      <ReviewDock
        selected={selection}
        prompt={prompt}
        preset={preset}
        presetCatalog={presets.data}
        presetStatus={presets.status}
        onPresetChange={setPreset}
        onRetryPresets={refetchPresets}
        onPromptChange={setPrompt}
        onRemove={(url) =>
          setSelection((prev) => prev.filter((item) => item.url !== url))
        }
        onClear={() => setSelection([])}
        onOpenSession={onOpenSession}
      />
    </div>
  )
}
