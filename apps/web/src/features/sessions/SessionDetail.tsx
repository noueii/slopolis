import { useEffect, useMemo, useState } from "react"
import { ArrowLeft, ExternalLink, ShieldCheck } from "lucide-react"

import type {
  ReviewSession,
  SessionStatus,
  TargetStatus,
} from "@/api/contract"
import { TERMINAL_SESSION_STATUSES } from "@/api/events"
import { Button } from "@/components/ui/button"
import { Progress } from "@/components/ui/progress"
import { Skeleton } from "@/components/ui/skeleton"
import { cn } from "@/lib/utils"
import { SessionStatusBadge } from "./components/SessionStatusBadge"
import {
  formatAbsoluteTime,
  formatCost,
  formatDuration,
  formatTokens,
} from "./lib/format"
import {
  useSessionEvents,
  type SessionEventUpdate,
  type SessionEventsMode,
} from "./lib/useSessionEvents"
import { useSession } from "./lib/useSessions"
import { SessionsError } from "./SessionsError"

export interface SessionDetailProps {
  sessionId: string
  onBack: () => void
}

const TERMINAL_TARGET_STATUSES: ReadonlySet<TargetStatus> = new Set([
  "done",
  "failed",
  "cancelled",
  "skipped",
])

const MODE_META: Record<
  SessionEventsMode,
  { label: string; tone: string; dot: string }
> = {
  idle: {
    label: "Idle",
    tone: "text-muted-foreground",
    dot: "bg-muted-foreground/50",
  },
  connecting: {
    label: "Connecting",
    tone: "text-muted-foreground",
    dot: "bg-warning animate-pulse",
  },
  live: { label: "Live", tone: "text-info", dot: "bg-info animate-pulse-ring" },
  polling: {
    label: "Polling",
    tone: "text-muted-foreground",
    dot: "bg-warning animate-pulse",
  },
}

function liveTargetStatus(
  live: SessionEventUpdate | null,
  targetId: string,
): TargetStatus | undefined {
  return live?.targets.find((target) => target.id === targetId)?.status
}

function MetaCell({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col gap-1 rounded-lg border border-border bg-card px-3 py-2.5">
      <span className="text-2xs font-semibold uppercase tracking-widest text-muted-foreground">
        {label}
      </span>
      <span className="font-mono text-[13px] text-foreground">{value}</span>
    </div>
  )
}

function LiveIndicator({ mode }: { mode: SessionEventsMode }) {
  const meta = MODE_META[mode]
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border border-border bg-muted/40 px-2 py-0.5 text-2xs font-semibold uppercase tracking-wider",
        meta.tone,
      )}
    >
      <span className={cn("size-1.5 rounded-full", meta.dot)} />
      {meta.label}
    </span>
  )
}

function DetailBody({
  session,
  live,
}: {
  session: ReviewSession
  live: SessionEventUpdate | null
}) {
  const status: SessionStatus = live?.status ?? session.status

  const targets = useMemo(
    () =>
      session.targets.map((target) => ({
        ...target,
        status: liveTargetStatus(live, target.id) ?? target.status,
      })),
    [session.targets, live],
  )

  const settled = targets.filter((target) =>
    TERMINAL_TARGET_STATUSES.has(target.status),
  ).length
  const progress =
    targets.length === 0
      ? status === "done"
        ? 100
        : 0
      : Math.round((settled / targets.length) * 100)

  return (
    <div className="flex flex-col gap-5">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <MetaCell label="Model" value={session.model} />
        <MetaCell label="Provider" value={session.provider} />
        <MetaCell label="Tokens" value={formatTokens(session.tokens)} />
        <MetaCell label="Cost" value={formatCost(session.costUsd)} />
        <MetaCell label="Duration" value={formatDuration(session.durationMs)} />
        <MetaCell
          label="Triggered by"
          value={`@${session.triggeredBy.handle}`}
        />
        <MetaCell label="Created" value={formatAbsoluteTime(session.createdAt)} />
        <MetaCell label="Findings" value={String(session.findingsCount)} />
      </div>

      {session.triggeredBy.isAdmin ? (
        <div className="flex items-center gap-2 rounded-lg border border-border bg-muted/30 px-3 py-2 text-2xs text-muted-foreground">
          <ShieldCheck className="size-3.5 text-info" />
          <span>
            Requested by a workspace admin ({session.triggeredBy.name}).
          </span>
        </div>
      ) : null}

      <section className="flex flex-col gap-2">
        <div className="flex items-center justify-between gap-3">
          <h2 className="text-2xs font-semibold uppercase tracking-widest text-muted-foreground">
            Progress
          </h2>
          <span className="tabular font-mono text-xs text-muted-foreground">
            {settled}/{targets.length} targets · {progress}%
          </span>
        </div>
        <Progress value={progress} aria-label="Session progress" />
      </section>

      <section className="flex flex-col gap-2">
        <h2 className="text-2xs font-semibold uppercase tracking-widest text-muted-foreground">
          Prompt
        </h2>
        <div className="rounded-lg border border-border bg-muted/40 px-4 py-3 text-[13px] leading-relaxed">
          {session.prompt ? (
            session.prompt
          ) : (
            <span className="text-muted-foreground">
              No custom prompt — built-in review behavior only.
            </span>
          )}
        </div>
      </section>

      <section className="flex flex-col gap-2">
        <h2 className="text-2xs font-semibold uppercase tracking-widest text-muted-foreground">
          Targets · {session.targetCount}
        </h2>
        {targets.length === 0 ? (
          <div className="rounded-lg border border-dashed border-border bg-card/50 px-4 py-6 text-[13px] text-muted-foreground">
            This session has no pull request targets yet.
          </div>
        ) : (
          <div className="divide-y divide-border overflow-hidden rounded-lg border border-border bg-card">
            {targets.map((target) => (
              <div
                key={target.id}
                className="flex flex-col gap-2 px-4 py-3 sm:flex-row sm:items-center sm:gap-4"
              >
                <span className="flex min-w-0 flex-1 flex-col gap-0.5">
                  <a
                    href={target.url}
                    target="_blank"
                    rel="noreferrer"
                    className="flex items-center gap-1.5 font-mono text-xs text-foreground hover:text-accent"
                  >
                    {target.repository.fullName}#{target.number}
                    {target.repository.private ? (
                      <span className="rounded border border-border px-1 text-[9px] uppercase tracking-wider text-muted-foreground">
                        private
                      </span>
                    ) : null}
                    <ExternalLink className="size-3 text-muted-foreground/60" />
                  </a>
                  <span className="truncate text-[13px] text-muted-foreground">
                    {target.title}
                  </span>
                </span>
                <div className="flex flex-wrap items-center gap-x-4 gap-y-2 sm:justify-end">
                  <span className="text-2xs text-muted-foreground">
                    {target.findingsCount} findings
                  </span>
                  <span className="tabular font-mono text-xs text-foreground/90">
                    {formatTokens(target.tokens)}
                  </span>
                  <span className="tabular font-mono text-xs text-foreground/90">
                    {formatCost(target.costUsd)}
                  </span>
                  <span className="tabular font-mono text-xs text-muted-foreground/80">
                    {formatDuration(target.durationMs)}
                  </span>
                  <SessionStatusBadge status={target.status} />
                </div>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  )
}

export function SessionDetail({ sessionId, onBack }: SessionDetailProps) {
  const { data, status, error, refetch } = useSession(sessionId)
  const [live, setLive] = useState<SessionEventUpdate | null>(null)
  const mode = useSessionEvents(sessionId, { onUpdate: setLive })

  const liveStatus = live?.status
  useEffect(() => {
    if (liveStatus && TERMINAL_SESSION_STATUSES.has(liveStatus)) refetch()
  }, [liveStatus, refetch])

  return (
    <div className="mx-auto flex w-full max-w-[1100px] animate-fade-up flex-col gap-5 p-6">
      <Button
        variant="ghost"
        size="sm"
        className="-ml-2 w-fit gap-1.5 text-muted-foreground"
        onClick={onBack}
      >
        <ArrowLeft data-icon="inline-start" />
        Back to sessions
      </Button>

      {status === "error" ? (
        <SessionsError
          message={error ?? "Could not load the session."}
          onRetry={refetch}
        />
      ) : null}

      {status !== "error" && !data ? (
        <div className="flex flex-col gap-4">
          <Skeleton className="h-6 w-[360px]" />
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            {Array.from({ length: 8 }).map((_, index) => (
              <Skeleton key={index} className="h-14 rounded-lg" />
            ))}
          </div>
          <Skeleton className="h-24 rounded-lg" />
        </div>
      ) : null}

      {data ? (
        <>
          <header className="flex flex-wrap items-center gap-3">
            <h1 className="text-xl font-semibold tracking-tight">{data.name}</h1>
            <SessionStatusBadge status={live?.status ?? data.status} />
            <LiveIndicator mode={mode} />
            <span className="font-mono text-xs text-muted-foreground">
              {data.id}
            </span>
          </header>
          <p className="text-sm text-muted-foreground">{data.title}</p>
          <DetailBody session={data} live={live} />
        </>
      ) : null}
    </div>
  )
}
