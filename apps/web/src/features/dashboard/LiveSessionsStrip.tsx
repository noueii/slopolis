import { useEffect, useState } from "react"
import { ArrowRight, ExternalLink } from "lucide-react"

import type { LiveSession } from "@/api/contract"
import { Button } from "@/components/ui/button"
import { Progress } from "@/components/ui/progress"
import { SessionStatusBadge } from "@/features/sessions/components/SessionStatusBadge"
import { formatDuration } from "@/features/sessions/lib/format"
import { RepositoryMark } from "./components/RepositoryMark"

const VISIBLE_LIMIT = 6

export interface LiveSessionsStripProps {
  sessions: LiveSession[]
  onOpenSession: (id: string) => void
  onViewAll?: () => void
}

export function LiveSessionsStrip({
  sessions,
  onOpenSession,
  onViewAll,
}: LiveSessionsStripProps) {
  const [, setTick] = useState(0)

  useEffect(() => {
    const timer = window.setInterval(() => setTick((value) => value + 1), 1000)
    return () => window.clearInterval(timer)
  }, [])

  if (sessions.length === 0) return null

  const visible = sessions.slice(0, VISIBLE_LIMIT)
  const overflow = sessions.length - visible.length

  return (
    <section className="flex flex-col gap-3" aria-label="Running reviews">
      <header className="flex items-center gap-2">
        <span className="relative flex size-2">
          <span className="absolute inline-flex size-full animate-ping rounded-full bg-running/50" />
          <span className="relative inline-flex size-2 rounded-full bg-running" />
        </span>
        <h2 className="text-2xs font-semibold uppercase tracking-widest text-muted-foreground">
          Running now
        </h2>
        <span className="rounded-full border border-border bg-muted/50 px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground">
          {sessions.length}
        </span>
      </header>

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {visible.map((session) => (
          <LiveCard
            key={session.id}
            session={session}
            onOpen={() => onOpenSession(session.id)}
          />
        ))}
        {overflow > 0 ? (
          <button
            type="button"
            onClick={onViewAll}
            className="flex min-h-[148px] flex-col items-center justify-center gap-1 rounded-lg border border-dashed border-border bg-card/50 p-3.5 text-center transition-colors hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <span className="font-mono text-sm font-semibold text-foreground">
              +{overflow}
            </span>
            <span className="text-2xs text-muted-foreground">
              more running
            </span>
          </button>
        ) : null}
      </div>
    </section>
  )
}

interface LiveCardProps {
  session: LiveSession
  onOpen: () => void
}

function LiveCard({ session, onOpen }: LiveCardProps) {
  const elapsed = Math.max(
    session.elapsedMs,
    Date.now() - new Date(session.startedAt).getTime(),
  )

  return (
    <article className="flex flex-col gap-2.5 rounded-lg border border-border bg-card p-3.5 transition-colors hover:border-running/40">
      <div className="flex items-center gap-2">
        <RepositoryMark
          fullName={session.repository.fullName}
          private={session.repository.private}
          size="sm"
        />
        <span className="flex min-w-0 flex-1 flex-col">
          <span className="truncate font-mono text-[12px] text-foreground">
            {session.repository.fullName}
          </span>
          <span className="font-mono text-[10px] text-muted-foreground">
            #{session.number}
          </span>
        </span>
        <span className="tabular shrink-0 font-mono text-[11px] text-muted-foreground">
          {formatDuration(elapsed)}
        </span>
      </div>

      <a
        href={session.url}
        target="_blank"
        rel="noreferrer"
        className="group/link inline-flex items-start gap-1.5 text-[13px] leading-snug text-muted-foreground transition-colors hover:text-foreground"
      >
        <span className="line-clamp-1">{session.title}</span>
        <ExternalLink className="mt-0.5 size-3 shrink-0 text-muted-foreground/60 group-hover/link:text-foreground" />
      </a>

      <Progress
        value={session.progress}
        className="h-1 bg-running/15 [&>div]:bg-running"
      />

      <div className="flex items-center justify-between gap-2">
        <span className="truncate text-2xs text-muted-foreground">
          {session.step}
        </span>
        <span className="tabular shrink-0 font-mono text-[11px] font-medium text-running">
          {session.progress}%
        </span>
      </div>

      <div className="flex items-center justify-between gap-2 border-t border-border/70 pt-2">
        <span className="truncate font-mono text-[10px] text-muted-foreground">
          {session.model}
        </span>
        <div className="flex shrink-0 items-center gap-1.5">
          <SessionStatusBadge status="running" showDot />
          <Button
            variant="ghost"
            size="sm"
            className="h-6 gap-1 px-2 text-[11px] text-muted-foreground hover:text-foreground"
            onClick={onOpen}
            aria-label={`Open session ${session.name}`}
          >
            Open
            <ArrowRight data-icon="inline-end" />
          </Button>
        </div>
      </div>
    </article>
  )
}
