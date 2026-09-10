import { Activity, ArrowLeft, ExternalLink } from "lucide-react"

import type { ReviewSession } from "@/api/contract"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { SessionStatusBadge } from "./components/SessionStatusBadge"
import {
  formatAbsoluteTime,
  formatCost,
  formatDuration,
  formatTokens,
} from "./lib/format"
import { useSession } from "./lib/useSessions"
import { SessionsError } from "./SessionsError"

export interface SessionDetailStubProps {
  sessionId: string
  onBack: () => void
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

function DetailBody({ session }: { session: ReviewSession }) {
  return (
    <div className="flex flex-col gap-5">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <MetaCell label="Model" value={session.model} />
        <MetaCell label="Provider" value={session.provider} />
        <MetaCell label="Tokens" value={formatTokens(session.tokens)} />
        <MetaCell label="Cost" value={formatCost(session.costUsd)} />
        <MetaCell label="Duration" value={formatDuration(session.durationMs)} />
        <MetaCell label="Triggered by" value={`@${session.triggeredBy.handle}`} />
        <MetaCell label="Created" value={formatAbsoluteTime(session.createdAt)} />
        <MetaCell label="Findings" value={String(session.findingsCount)} />
      </div>

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
        <div className="divide-y divide-border overflow-hidden rounded-lg border border-border bg-card">
          {session.targets.map((target) => (
            <div
              key={target.id}
              className="flex flex-col gap-2 px-4 py-3 sm:flex-row sm:items-center sm:gap-4"
            >
              <span className="flex min-w-0 flex-1 flex-col gap-0.5">
                <span className="flex items-center gap-1.5 font-mono text-xs text-foreground">
                  {target.repository.fullName}#{target.number}
                  {target.repository.private ? (
                    <span className="rounded border border-border px-1 text-[9px] uppercase tracking-wider text-muted-foreground">
                      private
                    </span>
                  ) : null}
                  <ExternalLink className="size-3 text-muted-foreground/60" />
                </span>
                <span className="truncate text-[13px] text-muted-foreground">
                  {target.title}
                </span>
              </span>
              <div className="flex items-center gap-4 sm:justify-end">
                <span className="text-2xs text-muted-foreground">
                  {target.findingsCount} findings
                </span>
                <span className="tabular font-mono text-xs text-foreground/90">
                  {formatTokens(target.tokens)}
                </span>
                <span className="tabular font-mono text-xs text-foreground/90">
                  {formatCost(target.costUsd)}
                </span>
                <SessionStatusBadge status={target.status} />
              </div>
            </div>
          ))}
        </div>
      </section>

      <div className="flex items-start gap-3 rounded-lg border border-dashed border-border bg-card/50 px-4 py-3.5">
        <Activity className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
        <p className="text-[13px] leading-relaxed text-muted-foreground">
          Live progress, the findings timeline, and raw model payloads stream in
          over SSE in a later pass. This is a structural stub on the real
          contract.
        </p>
      </div>
    </div>
  )
}

export function SessionDetailStub({
  sessionId,
  onBack,
}: SessionDetailStubProps) {
  const { data, status, error, refetch } = useSession(sessionId)

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
            <SessionStatusBadge status={data.status} />
            <span className="font-mono text-xs text-muted-foreground">
              {data.id}
            </span>
          </header>
          <DetailBody session={data} />
        </>
      ) : null}
    </div>
  )
}
