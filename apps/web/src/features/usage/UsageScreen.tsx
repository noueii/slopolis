import { RefreshCw } from "lucide-react"

import { cn } from "@/lib/utils"
import type { UsageResponse } from "@/api/contract"
import { Button } from "@/components/ui/button"
import { UsageBreakdownTable } from "./components/UsageBreakdownTable"
import { UsageSeriesChart } from "./components/UsageSeriesChart"
import { UsageTotals } from "./components/UsageTotals"
import { UsageEmpty, UsageError, UsageSkeleton } from "./components/UsageStates"
import { useUsage } from "./lib/useUsage"

export interface UsageScreenProps {
  onNewReview: () => void
}

/**
 * With nothing attributed anywhere there is no report to show, so the page
 * offers the one action that produces usage instead of empty tables.
 */
function hasNothingRecorded(usage: UsageResponse): boolean {
  return (
    usage.totalSessions === 0 &&
    usage.totalTokens === 0 &&
    usage.totalCostUsd === 0 &&
    usage.series.length === 0 &&
    usage.byModel.length === 0 &&
    usage.byRepository.length === 0 &&
    usage.byUser.length === 0
  )
}

export function UsageScreen({ onNewReview }: UsageScreenProps) {
  const { data, status, error, refetch } = useUsage()
  const busy = status === "loading"
  const loading = busy && !data
  const failed = status === "error"
  const empty = !failed && data !== null && hasNothingRecorded(data)
  const report = !failed && data !== null && !empty ? data : null

  return (
    <div
      className="mx-auto flex w-full max-w-[1180px] animate-fade-up flex-col gap-6 p-6"
      aria-busy={busy}
    >
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div className="flex flex-col gap-1">
          <p className="font-mono text-2xs uppercase tracking-widest text-muted-foreground">
            Workspace
          </p>
          <h1 className="text-xl font-semibold tracking-tight">Usage</h1>
          <p className="max-w-2xl text-sm text-muted-foreground">
            What review sessions have consumed, broken down by model,
            repository, and reviewer. Cost is what the provider reported, or an
            estimate from the token count when no price came back.
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={refetch}>
          <RefreshCw data-icon="inline-start" className={cn(busy && "animate-spin")} />
          Refresh
        </Button>
      </header>

      {loading ? <UsageSkeleton /> : null}

      {failed ? (
        <UsageError
          message={error ?? "Something went wrong while loading usage."}
          onRetry={refetch}
        />
      ) : null}

      {empty ? <UsageEmpty onNewReview={onNewReview} /> : null}

      {report ? (
        <>
          <UsageTotals
            tokens={report.totalTokens}
            costUsd={report.totalCostUsd}
            sessions={report.totalSessions}
          />

          <UsageSeriesChart series={report.series} />

          <UsageBreakdownTable
            title="By model"
            dimension="Model"
            emptyMessage="No usage attributed to a model yet."
            rows={report.byModel}
            total={{ tokens: report.totalTokens, costUsd: report.totalCostUsd }}
          />

          <UsageBreakdownTable
            title="By repository"
            dimension="Repository"
            emptyMessage="No usage attributed to a repository yet."
            rows={report.byRepository}
            total={{ tokens: report.totalTokens, costUsd: report.totalCostUsd }}
          />

          <UsageBreakdownTable
            title="By user"
            dimension="User"
            emptyMessage="No usage attributed to a user yet."
            rows={report.byUser}
            total={{ tokens: report.totalTokens, costUsd: report.totalCostUsd }}
          />
        </>
      ) : null}
    </div>
  )
}
