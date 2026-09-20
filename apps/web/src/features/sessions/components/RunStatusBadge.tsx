import type { AgentRunLevel, AgentRunStatus } from "@/api/contract"
import { cn } from "@/lib/utils"

import { RUN_STATUS_LABELS } from "../lib/runTree"

/**
 * Run statuses are a different vocabulary from session statuses (`pending`
 * rather than `queued`), so the badge is its own small component rather than a
 * mapping onto `SessionStatusBadge` that would relabel a run.
 */
const STATUS_STYLES: Record<AgentRunStatus, string> = {
  pending: "border-border/80 bg-muted/70 text-muted-foreground",
  running: "border-info/30 bg-info/10 text-info",
  done: "border-success/30 bg-success/10 text-success",
  failed: "border-destructive/30 bg-destructive/10 text-destructive",
  cancelled: "border-warning/30 bg-warning/10 text-warning",
}

const STATUS_DOTS: Record<AgentRunStatus, string> = {
  pending: "bg-muted-foreground/60",
  running: "bg-info animate-pulse-ring",
  done: "bg-success",
  failed: "bg-destructive",
  cancelled: "bg-warning",
}

const LEVEL_STYLES: Record<AgentRunLevel, string> = {
  main: "border-accent/40 bg-accent/5 text-accent",
  pr: "border-info/40 bg-info/5 text-info",
  sub: "border-border/80 bg-muted/50 text-muted-foreground",
}

const LEVEL_LABELS: Record<AgentRunLevel, string> = {
  main: "Main",
  pr: "PR",
  sub: "Sub",
}

export function RunStatusBadge({
  status,
  className,
}: {
  status: AgentRunStatus
  className?: string
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-2xs font-semibold uppercase tracking-wider",
        STATUS_STYLES[status],
        className,
      )}
    >
      <span className={cn("size-1.5 rounded-full", STATUS_DOTS[status])} />
      {RUN_STATUS_LABELS[status]}
    </span>
  )
}

export function RunLevelChip({
  level,
  className,
}: {
  level: AgentRunLevel
  className?: string
}) {
  return (
    <span
      className={cn(
        "rounded border px-1 py-px text-[9px] font-semibold uppercase tracking-wider",
        LEVEL_STYLES[level],
        className,
      )}
    >
      {LEVEL_LABELS[level]}
    </span>
  )
}
