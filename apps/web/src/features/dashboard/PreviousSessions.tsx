import { ArrowRight, ExternalLink, MessageSquareText } from "lucide-react"

import { cn } from "@/lib/utils"
import type { DashboardSession, SessionTarget } from "@/api/contract"
import { Button } from "@/components/ui/button"
import { SessionStatusBadge } from "@/features/sessions/components/SessionStatusBadge"
import {
  formatCost,
  formatRelativeTime,
} from "@/features/sessions/lib/format"
import { RepositoryMark } from "./components/RepositoryMark"

export interface PreviousSessionsProps {
  sessions: DashboardSession[]
  onOpenSession: (id: string) => void
  onViewAll: () => void
}

export function PreviousSessions({
  sessions,
  onOpenSession,
  onViewAll,
}: PreviousSessionsProps) {
  return (
    <section className="flex flex-col gap-3" aria-label="Recent reviews">
      <header className="flex items-center justify-between gap-2">
        <h2 className="text-2xs font-semibold uppercase tracking-widest text-muted-foreground">
          Recent reviews
        </h2>
        <Button
          variant="ghost"
          size="sm"
          className="h-7 gap-1.5 px-2 text-[13px] text-muted-foreground"
          onClick={onViewAll}
        >
          All sessions
          <ArrowRight data-icon="inline-end" />
        </Button>
      </header>

      <div className="divide-y divide-border overflow-hidden rounded-xl border border-border bg-card">
        {sessions.map((session) => (
          <ConversationRow
            key={session.id}
            session={session}
            onOpen={() => onOpenSession(session.id)}
          />
        ))}
      </div>
    </section>
  )
}

interface ConversationRowProps {
  session: DashboardSession
  onOpen: () => void
}

function ConversationRow({ session, onOpen }: ConversationRowProps) {
  return (
    <div className="flex w-full items-start gap-3.5 px-4 py-3.5 transition-colors hover:bg-muted/30">
      <span
        className={cn(
          "mt-0.5 grid size-7 shrink-0 place-items-center rounded-full border",
          session.status === "failed"
            ? "border-destructive/30 bg-destructive/10 text-destructive"
            : session.status === "running"
              ? "border-running/30 bg-running/10 text-running"
              : "border-border bg-muted/60 text-muted-foreground",
        )}
      >
        <MessageSquareText className="size-3.5" />
      </span>

      <div className="flex min-w-0 flex-1 flex-col gap-2">
        <div className="flex items-center gap-2">
          <span className="truncate text-[13px] font-medium text-foreground">
            {session.name}
          </span>
          <SessionStatusBadge status={session.status} className="shrink-0" />
        </div>

        {session.prompt ? (
          <p className="line-clamp-1 text-[13px] italic text-muted-foreground">
            “{session.prompt}”
          </p>
        ) : (
          <p className="text-[13px] text-muted-foreground">
            No custom focus — built-in review only.
          </p>
        )}

        <div className="flex flex-col gap-1.5">
          <TargetRows targets={session.targets} total={session.targetCount} />
        </div>
      </div>

      <div className="flex shrink-0 flex-col items-end gap-1.5">
        <span className="whitespace-nowrap text-2xs text-muted-foreground">
          {formatRelativeTime(session.createdAt)}
        </span>
        {session.findingsCount > 0 ? (
          <span className="whitespace-nowrap font-mono text-[10px] text-muted-foreground">
            {session.findingsCount} finding
            {session.findingsCount === 1 ? "" : "s"}
          </span>
        ) : null}
        <span className="tabular whitespace-nowrap font-mono text-[11px] text-foreground/80">
          {formatCost(session.costUsd)}
        </span>
        <Button
          variant="ghost"
          size="sm"
          className="h-6 gap-1 px-2 text-[11px] text-muted-foreground hover:text-foreground"
          onClick={onOpen}
          aria-label={`Open session ${session.name}`}
        >
          Open session
          <ArrowRight data-icon="inline-end" />
        </Button>
      </div>
    </div>
  )
}

function TargetRows({
  targets,
  total,
}: {
  targets: SessionTarget[]
  total: number
}) {
  const visible = targets.slice(0, 2)
  const overflow = total - visible.length

  return (
    <>
      {visible.map((target) => (
        <div key={target.id} className="flex min-w-0 items-center gap-2">
          <RepositoryMark
            fullName={target.repository.fullName}
            private={target.repository.private}
            size="sm"
          />
          <span className="hidden shrink-0 font-mono text-[10px] text-muted-foreground sm:inline">
            {target.repository.fullName}
          </span>
          <a
            href={target.url}
            target="_blank"
            rel="noreferrer"
            className="group/link inline-flex min-w-0 items-center gap-1 text-[12px] text-foreground/90 transition-colors hover:text-accent"
          >
            <span className="shrink-0 font-mono text-[11px] text-muted-foreground">
              #{target.number}
            </span>
            <span className="truncate">{target.title}</span>
            <ExternalLink className="size-3 shrink-0 text-muted-foreground/50 group-hover/link:text-accent" />
          </a>
        </div>
      ))}
      {overflow > 0 ? (
        <span className="font-mono text-[10px] text-muted-foreground">
          +{overflow} more
        </span>
      ) : null}
    </>
  )
}
