import type { SessionStatus, TargetStatus } from "@/api/contract"

export const SESSION_STATUSES: SessionStatus[] = [
  "queued",
  "running",
  "done",
  "failed",
  "cancelled",
]

export const STATUS_LABELS: Record<SessionStatus, string> = {
  queued: "Queued",
  running: "Running",
  done: "Done",
  failed: "Failed",
  cancelled: "Cancelled",
}

export const TARGET_STATUS_LABELS: Record<TargetStatus, string> = {
  queued: "Queued",
  running: "Running",
  done: "Done",
  failed: "Failed",
  cancelled: "Cancelled",
  skipped: "Skipped",
}
