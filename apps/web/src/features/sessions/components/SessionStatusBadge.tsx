import { cn } from "@/lib/utils"
import type { SessionStatus, TargetStatus } from "@/api/contract"
import { STATUS_LABELS } from "@/features/sessions/lib/status"

const STYLES: Record<SessionStatus, string> = {
  queued: "border-border/80 bg-muted/70 text-muted-foreground",
  running: "border-info/30 bg-info/10 text-info",
  done: "border-success/30 bg-success/10 text-success",
  failed: "border-destructive/30 bg-destructive/10 text-destructive",
  cancelled: "border-warning/30 bg-warning/10 text-warning",
}

const DOT_STYLES: Record<SessionStatus, string> = {
  queued: "bg-muted-foreground/60",
  running: "bg-info animate-pulse-ring",
  done: "bg-success",
  failed: "bg-destructive",
  cancelled: "bg-warning",
}

export interface SessionStatusBadgeProps {
  status: SessionStatus | TargetStatus
  className?: string
  showDot?: boolean
}

export function SessionStatusBadge({
  status,
  className,
  showDot = true,
}: SessionStatusBadgeProps) {
  const normalized = status as SessionStatus
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-2xs font-semibold uppercase tracking-wider",
        STYLES[normalized] ?? STYLES.queued,
        className,
      )}
    >
      {showDot ? (
        <span
          className={cn(
            "size-1.5 rounded-full",
            DOT_STYLES[normalized] ?? DOT_STYLES.queued,
          )}
        />
      ) : null}
      {STATUS_LABELS[normalized] ?? status}
    </span>
  )
}
