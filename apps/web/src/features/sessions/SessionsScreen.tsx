import { useEffect, useMemo, useRef, useState } from "react"
import { RefreshCw } from "lucide-react"

import { cn } from "@/lib/utils"
import type { MockScenario } from "@/api/client"
import type { SessionListParams, SessionSort } from "@/api/contract"
import { Button } from "@/components/ui/button"
import { formatCost, formatCount } from "./lib/format"
import {
  useDebouncedValue,
  useSessionFilterOptions,
  useSessionStats,
  useSessions,
} from "./lib/useSessions"
import { SessionFilters } from "./SessionFilters"
import { SessionsEmpty } from "./SessionsEmpty"
import { SessionsError } from "./SessionsError"
import { SessionsPagination } from "./SessionsPagination"
import { SessionsSkeleton } from "./SessionsSkeleton"
import { SessionsTable } from "./SessionsTable"

export interface SessionsScreenProps {
  onOpenSession: (id: string) => void
  onNewReview: () => void
  scenario: MockScenario
}

const BASE_PARAMS: SessionListParams = {
  page: 1,
  pageSize: 25,
  sort: "created_desc",
}

export function SessionsScreen({
  onOpenSession,
  onNewReview,
  scenario,
}: SessionsScreenProps) {
  const [params, setParams] = useState<SessionListParams>(BASE_PARAMS)
  const [searchInput, setSearchInput] = useState("")
  const debouncedSearch = useDebouncedValue(searchInput, 250)

  const { data, status, error, refetch } = useSessions(params)
  const filterOptions = useSessionFilterOptions()
  const stats = useSessionStats()
  const { refetch: refetchStats } = stats
  const { refetch: refetchFilters } = filterOptions

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
    refetchStats()
    refetchFilters()
  }, [scenario, refetch, refetchStats, refetchFilters])

  const activeCount = useMemo(() => {
    const rangeActive = params.range && params.range !== "all"
    return [params.q, params.repo, params.user, params.status, rangeActive].filter(
      Boolean,
    ).length
  }, [params])

  const setFilter = (patch: Partial<SessionListParams>) =>
    setParams((prev) => ({ ...prev, ...patch, page: 1 }))

  const handleClear = () => {
    setSearchInput("")
    setParams({
      page: 1,
      pageSize: params.pageSize,
      sort: params.sort,
    })
  }

  const handleRefresh = () => {
    refetch()
    refetchStats()
  }

  const showSkeleton = status === "loading" && !data
  const showError = status === "error"
  const showEmpty = !showError && data !== null && data.items.length === 0
  const showTable = !showError && data !== null && data.items.length > 0
  const busy = status === "loading"

  return (
    <div
      className="mx-auto flex w-full max-w-[1500px] animate-fade-up flex-col gap-5 p-6"
      aria-busy={busy}
    >
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div className="flex flex-col gap-1">
          <h1 className="text-xl font-semibold tracking-tight">Sessions</h1>
          <p className="text-sm text-muted-foreground">
            {stats.data
              ? `${formatCount(stats.data.totalSessions)} sessions · ${stats.data.running} running · ${stats.data.failed} failed`
              : stats.status === "error"
                ? "Session activity unavailable"
                : "Loading session activity…"}
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={handleRefresh}>
          <RefreshCw
            data-icon="inline-start"
            className={cn(busy && "animate-spin")}
          />
          Refresh
        </Button>
      </header>

      <StatStrip
        total={stats.data?.totalSessions}
        running={stats.data?.running}
        failed={stats.data?.failed}
        costUsd={stats.data?.costUsd}
      />

      <SessionFilters
        filters={filterOptions.data}
        searchInput={searchInput}
        onSearchChange={setSearchInput}
        params={params}
        onFilterChange={setFilter}
        onClear={handleClear}
        activeCount={activeCount}
      />

      {showSkeleton ? <SessionsSkeleton /> : null}

      {showError ? (
        <SessionsError
          message={error ?? "Something went wrong while loading sessions."}
          onRetry={refetch}
        />
      ) : null}

      {showEmpty ? (
        <SessionsEmpty
          filtered={activeCount > 0}
          onClear={handleClear}
          onNewReview={onNewReview}
        />
      ) : null}

      {showTable ? (
        <div
          className={cn(
            "overflow-hidden rounded-lg border border-border bg-card transition-opacity",
            busy && "pointer-events-none opacity-60",
          )}
        >
          <SessionsTable
            sessions={data.items}
            sort={params.sort ?? "created_desc"}
            onSortChange={(sort: SessionSort) => setFilter({ sort })}
            onOpenSession={onOpenSession}
          />
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
    </div>
  )
}

interface StatStripProps {
  total?: number
  running?: number
  failed?: number
  costUsd?: number
}

function StatStrip({ total, running, failed, costUsd }: StatStripProps) {
  const cells = [
    {
      label: "Total sessions",
      value: total === undefined ? "—" : formatCount(total),
      tone: "default" as const,
    },
    {
      label: "Running",
      value: running === undefined ? "—" : String(running),
      tone: "info" as const,
    },
    {
      label: "Failed",
      value: failed === undefined ? "—" : String(failed),
      tone: "destructive" as const,
    },
    {
      label: "Tracked spend",
      value: costUsd === undefined ? "—" : formatCost(costUsd),
      tone: "default" as const,
    },
  ]

  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
      {cells.map((cell) => (
        <div
          key={cell.label}
          className="flex flex-col gap-1 rounded-lg border border-border bg-card px-4 py-3"
        >
          <span className="text-2xs font-semibold uppercase tracking-widest text-muted-foreground">
            {cell.label}
          </span>
          <span
            className={cn(
              "tabular font-mono text-lg font-semibold tracking-tight",
              cell.tone === "info" && "text-info",
              cell.tone === "destructive" && "text-destructive",
            )}
          >
            {cell.value}
          </span>
        </div>
      ))}
    </div>
  )
}
