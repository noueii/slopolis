import { RotateCw } from "lucide-react"
import { useMemo } from "react"

import type { AgentEventItem, AgentRunNode, SessionStatus } from "@/api/contract"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { cn } from "@/lib/utils"

import { formatAbsoluteTime, formatCost, formatDuration, formatTokens } from "../lib/format"
import { describeRunEvent, type RunEventTone } from "../lib/runEvents"
import { isPreviousAttempt, mergeRunEvents } from "../lib/runTree"
import { useRunNodeEvents } from "../lib/useRunNodeEvents"
import { RunLevelChip, RunStatusBadge } from "./RunStatusBadge"

export interface RunNodeDetailProps {
  sessionId: string | null
  run: AgentRunNode | null
  /** Session status: a run that ended while the session is in flight is history. */
  sessionStatus: SessionStatus
  /** Live stream buffer; events for other runs are ignored here. */
  liveEvents: AgentEventItem[]
}

const TONE_STYLES: Record<RunEventTone, string> = {
  neutral: "border-border/80 bg-muted/50 text-muted-foreground",
  info: "border-info/30 bg-info/10 text-info",
  success: "border-success/30 bg-success/10 text-success",
  warning: "border-warning/30 bg-warning/10 text-warning",
  danger: "border-destructive/30 bg-destructive/10 text-destructive",
}

/**
 * What a run's status means for the reader (spec v2 §11): a failed sub-agent is
 * a partial-coverage note, not an alarm, and never a session-level failure.
 */
function statusNote(run: AgentRunNode): string | null {
  if (run.status === "failed") {
    if (run.level === "sub") {
      return "This sub-agent failed. The run that spawned it continued with partial coverage — a failed sub-agent does not fail the session."
    }
    if (run.level === "pr") {
      return "This PR orchestrator failed. Its own children stopped there; the session's other targets kept their own runs."
    }
    return "The main orchestrator failed, which is what ends the session as failed. Per-target results recorded before that are kept."
  }
  if (run.status === "cancelled") {
    return "Cancelled. Cancellation reaches this run from its parent; whatever it had already recorded is kept."
  }
  if (run.status === "pending") {
    return "Waiting behind its parent — nothing of its own has run yet."
  }
  if (run.status === "running") {
    return "Running now; events appear here as the worker records them."
  }
  return null
}

function runDuration(run: AgentRunNode): string {
  if (run.startedAt && run.endedAt) {
    return formatDuration(new Date(run.endedAt).getTime() - new Date(run.startedAt).getTime())
  }
  return run.status === "running" ? "in progress" : "—"
}

export function RunNodeDetail({ sessionId, run, sessionStatus, liveEvents }: RunNodeDetailProps) {
  const { items, status, error, hasMore, loadingMore, loadMore, reload } =
    useRunNodeEvents(sessionId, run?.id ?? null)

  const runId = run?.id ?? null
  const events = useMemo(
    () =>
      mergeRunEvents(
        items,
        runId === null ? [] : liveEvents.filter((event) => event.runId === runId),
      ),
    [items, liveEvents, runId],
  )

  if (!run) {
    return (
      <section
        aria-label="Run details"
        className="flex min-h-[240px] items-center justify-center rounded-lg border border-dashed border-border bg-card/50 px-4 py-8"
      >
        <p className="text-[13px] text-muted-foreground">
          Select a run in the tree to read its event stream.
        </p>
      </section>
    )
  }

  const previousAttempt = isPreviousAttempt(sessionStatus, run.status)
  // A superseded run's status says what the last attempt did, so the note that
  // reads it as this session's outcome ("the main orchestrator failed, which
  // ends the session as failed") would contradict the queued session above it.
  const note = previousAttempt ? null : statusNote(run)

  return (
    <section
      aria-label="Run details"
      className="flex flex-col overflow-hidden rounded-lg border border-border bg-card"
    >
      <header className="flex flex-col gap-2 border-b border-border px-4 py-3">
        <div className="flex flex-wrap items-center gap-2">
          <RunLevelChip level={run.level} />
          <h2 className="text-[13px] font-semibold tracking-tight">{run.role}</h2>
          <RunStatusBadge status={run.status} />
          <span className="font-mono text-2xs text-muted-foreground">
            {run.modelId ?? "model pending"}
          </span>
        </div>
        <p className="text-[13px] text-muted-foreground">
          {run.objective || "No objective recorded for this run."}
        </p>
        <div className="flex flex-wrap gap-x-4 gap-y-1 font-mono text-2xs text-muted-foreground">
          <span>{formatTokens(run.tokens)} tokens</span>
          <span>{formatCost(run.costUsd)}</span>
          <span>
            started {run.startedAt ? formatAbsoluteTime(run.startedAt) : "—"}
          </span>
          <span>{runDuration(run)}</span>
        </div>
      </header>

      {previousAttempt ? (
        <div className="flex flex-wrap items-center gap-2 border-b border-border bg-muted/30 px-4 py-2">
          <Badge
            variant="outline"
            className="px-1.5 py-0 text-2xs font-semibold uppercase tracking-wider text-muted-foreground"
          >
            previous attempt
          </Badge>
          <p className="text-2xs leading-relaxed text-muted-foreground">
            This result is from the previous attempt, not the one now in
            flight — the retry reopens this run and records new events here.
          </p>
        </div>
      ) : note ? (
        <p className="border-b border-border bg-muted/30 px-4 py-2 text-2xs leading-relaxed text-muted-foreground">
          {note}
        </p>
      ) : null}

      {run.error ? (
        <p className="border-b border-border bg-destructive/5 px-4 py-2 font-mono text-2xs leading-relaxed text-destructive">
          {run.error}
        </p>
      ) : null}

      <div className="flex items-center justify-between gap-3 px-4 py-2">
        <h3 className="text-2xs font-semibold uppercase tracking-widest text-muted-foreground">
          Events
        </h3>
        {status === "loading" ? null : (
          <span className="tabular font-mono text-2xs text-muted-foreground">
            {events.length} {events.length === 1 ? "event" : "events"}
          </span>
        )}
      </div>

      {status === "loading" ? (
        <div className="flex flex-col gap-2 px-4 pb-4">
          {Array.from({ length: 4 }).map((_, index) => (
            <Skeleton key={index} className="h-9 rounded-lg" />
          ))}
        </div>
      ) : null}

      {status === "error" ? (
        <div className="mx-4 mb-4 flex flex-col items-start gap-2 rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-3">
          <p className="text-[13px] text-muted-foreground">
            {error ?? "Could not load this run's events."}
          </p>
          <Button variant="outline" size="sm" onClick={reload}>
            <RotateCw data-icon="inline-start" />
            Try again
          </Button>
        </div>
      ) : null}

      {status === "success" && events.length === 0 ? (
        <p className="px-4 pb-4 text-[13px] text-muted-foreground">
          No events recorded for this run yet.
        </p>
      ) : null}

      {events.length > 0 ? (
        <ol className="max-h-[26rem] overflow-y-auto border-t border-border">
          {events.map((event) => (
            <RunEventRow key={event.id} event={event} />
          ))}
        </ol>
      ) : null}

      {status === "success" && hasMore ? (
        <div className="flex items-center justify-between gap-3 border-t border-border px-4 py-2">
          <span className="text-2xs text-muted-foreground">
            A full page of events came back, so this run may hold more.
          </span>
          <Button
            variant="outline"
            size="sm"
            onClick={loadMore}
            disabled={loadingMore}
          >
            {loadingMore ? "Loading…" : "Load more"}
          </Button>
        </div>
      ) : null}

      {status === "success" && error ? (
        <div className="flex items-center justify-between gap-3 border-t border-border px-4 py-2">
          <span className="text-2xs text-destructive">{error}</span>
          <Button variant="outline" size="sm" onClick={loadMore}>
            Try again
          </Button>
        </div>
      ) : null}
    </section>
  )
}

function RunEventRow({ event }: { event: AgentEventItem }) {
  const view = describeRunEvent(event)
  return (
    <li className="flex flex-col gap-1 border-b border-border/60 px-4 py-2 last:border-b-0">
      <div className="flex items-center gap-2">
        <span
          className={cn(
            "rounded border px-1.5 py-px text-2xs font-semibold uppercase tracking-wider",
            TONE_STYLES[view.tone],
          )}
        >
          {view.label}
        </span>
        <span className="tabular font-mono text-2xs text-muted-foreground/70">
          #{event.seq}
        </span>
        <span className="ml-auto font-mono text-2xs text-muted-foreground/70">
          {formatAbsoluteTime(event.createdAt)}
        </span>
      </div>
      <p className="text-[13px] leading-relaxed text-foreground/90">
        {view.detail}
      </p>
    </li>
  )
}
