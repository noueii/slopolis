import { cn } from "@/lib/utils"
import type { DashboardSummary } from "@/api/contract"
import {
  formatCost,
  formatCount,
  formatRelativeTime,
  formatTokens,
} from "@/features/sessions/lib/format"

export interface AnalyticsStripProps {
  summary?: DashboardSummary
  generatedAt?: string
}

interface StatCell {
  label: string
  value: string
  hint: string
  tone?: "running" | "destructive"
  lead?: boolean
}

export function AnalyticsStrip({ summary, generatedAt }: AnalyticsStripProps) {
  const total = summary?.totalSessions ?? 0
  const spend = summary?.spendUsd ?? 0
  const avg = total > 0 ? spend / total : 0

  const cells: StatCell[] = [
    {
      label: "Tracked spend",
      value: summary ? formatCost(spend) : "—",
      hint: "all sessions",
      lead: true,
    },
    {
      label: "Sessions",
      value: summary ? formatCount(total) : "—",
      hint: "in scope",
    },
    {
      label: "Running",
      value: summary ? String(summary.running) : "—",
      hint: "live now",
      tone: "running",
    },
    {
      label: "Failed",
      value: summary ? String(summary.failed) : "—",
      hint: "need attention",
      tone: "destructive",
    },
    {
      label: "Avg / session",
      value: summary ? formatCost(avg) : "—",
      hint: "blended",
    },
    {
      label: "Tokens",
      value: summary ? formatTokens(summary.tokens) : "—",
      hint: "attributed",
    },
  ]

  return (
    <section className="flex flex-col gap-2" aria-label="Workspace analytics">
      <header className="flex items-baseline justify-between gap-3 px-0.5">
        <div className="flex items-baseline gap-2">
          <h2 className="text-2xs font-semibold uppercase tracking-[0.22em] text-muted-foreground">
            Workspace
          </h2>
          <span className="truncate font-mono text-[11px] text-muted-foreground/80">
            {summary?.scope ?? "All repositories"}
          </span>
        </div>
        {generatedAt ? (
          <span className="hidden shrink-0 font-mono text-2xs text-muted-foreground/70 sm:inline">
            synced {formatRelativeTime(generatedAt)}
          </span>
        ) : null}
      </header>

      <dl className="grid grid-cols-3 gap-px overflow-hidden rounded-lg border border-border bg-border lg:grid-cols-6">
        {cells.map((cell) => (
          <div
            key={cell.label}
            className={cn(
              "flex min-w-0 flex-col gap-1 bg-card px-3 py-2 sm:px-3.5 sm:py-2.5",
              cell.lead && "bg-accent/[0.05]",
            )}
          >
            <dt className="flex items-center gap-1.5 text-2xs font-semibold uppercase tracking-[0.16em] text-muted-foreground">
              {cell.tone || cell.lead ? (
                <span
                  aria-hidden
                  className={cn(
                    "size-1.5 rounded-full",
                    cell.tone === "running"
                      ? "bg-running"
                      : cell.tone === "destructive"
                        ? "bg-destructive"
                        : "bg-accent",
                  )}
                />
              ) : null}
              <span className="truncate">{cell.label}</span>
            </dt>
            <dd
              className={cn(
                "tabular truncate font-mono font-semibold tracking-tight text-foreground",
                cell.lead ? "text-[17px]" : "text-[15px]",
                cell.tone === "running" && "text-running",
                cell.tone === "destructive" && "text-destructive",
              )}
            >
              {cell.value}
            </dd>
            <span className="hidden truncate text-2xs text-muted-foreground/75 sm:block">
              {cell.hint}
            </span>
          </div>
        ))}
      </dl>
    </section>
  )
}
