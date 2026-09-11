import { useMemo, useState } from "react"
import { ChevronDown, Inbox } from "lucide-react"

import { cn } from "@/lib/utils"
import type {
  DashboardSession,
  LiveSession,
  SessionStatus,
} from "@/api/contract"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { SessionStatusBadge } from "@/features/sessions/components/SessionStatusBadge"
import { formatDuration, formatRelativeTime } from "@/features/sessions/lib/format"
import { RepositoryMark } from "./components/RepositoryMark"

const ALL_REPOS = "all"
const MAX_VISIBLE_REPOS = 3

type TabValue = "running" | "finished"

interface ReviewTarget {
  id: string
  fullName: string
  private: boolean
  headBranch: string
}

interface ReviewRow {
  id: string
  title: string
  name: string
  status: SessionStatus
  timestamp: string
  elapsedMs?: number
  targets: ReviewTarget[]
}

export interface ReviewListProps {
  running: LiveSession[]
  recent: DashboardSession[]
  onOpenSession: (id: string) => void
}

function liveToRow(session: LiveSession): ReviewRow {
  return {
    id: session.id,
    title: session.title,
    name: session.name,
    status: session.status,
    timestamp: session.startedAt,
    elapsedMs: session.elapsedMs,
    targets: [
      {
        id: `${session.id}:${session.prLabel}`,
        fullName: session.repository.fullName,
        private: session.repository.private,
        headBranch: session.headBranch,
      },
    ],
  }
}

function sessionToRow(session: DashboardSession): ReviewRow {
  return {
    id: session.id,
    title: session.title,
    name: session.name,
    status: session.status,
    timestamp: session.createdAt,
    targets: session.targets.map((target) => ({
      id: target.id,
      fullName: target.repository.fullName,
      private: target.repository.private,
      headBranch: target.headBranch,
    })),
  }
}

function isActiveStatus(status: SessionStatus): boolean {
  return status === "queued" || status === "running"
}

function rowMatchesRepo(row: ReviewRow, repo: string): boolean {
  return repo === ALL_REPOS || row.targets.some((t) => t.fullName === repo)
}

function targetKey(fullName: string, number: number): string {
  return `${fullName}#${number}`
}

export function ReviewList({ running, recent, onOpenSession }: ReviewListProps) {
  const [tab, setTab] = useState<TabValue | null>(null)
  const [repo, setRepo] = useState<string>(ALL_REPOS)

  const runningRows = useMemo(() => {
    const liveKeys = new Set(
      running.map((s) => targetKey(s.repository.fullName, s.number)),
    )
    const extraActive = recent.filter(
      (s) =>
        isActiveStatus(s.status) &&
        !s.targets.some((t) =>
          liveKeys.has(targetKey(t.repository.fullName, t.number)),
        ),
    )
    return [...running.map(liveToRow), ...extraActive.map(sessionToRow)]
  }, [running, recent])

  const finishedRows = useMemo(
    () => recent.filter((s) => !isActiveStatus(s.status)).map(sessionToRow),
    [recent],
  )

  const repoOptions = useMemo(() => {
    const names = new Set<string>()
    for (const row of [...runningRows, ...finishedRows]) {
      for (const target of row.targets) names.add(target.fullName)
    }
    return [...names].sort((a, b) => a.localeCompare(b))
  }, [runningRows, finishedRows])

  const visibleRunning = useMemo(
    () => runningRows.filter((row) => rowMatchesRepo(row, repo)),
    [runningRows, repo],
  )
  const visibleFinished = useMemo(
    () => finishedRows.filter((row) => rowMatchesRepo(row, repo)),
    [finishedRows, repo],
  )

  const activeTab: TabValue =
    tab ?? (visibleRunning.length > 0 ? "running" : "finished")

  return (
    <section aria-label="Review sessions">
      <Tabs
        value={activeTab}
        onValueChange={(value) => setTab(value as TabValue)}
        className="flex flex-col gap-3"
      >
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
          <TabsList className="h-8 self-start">
            <TabsTrigger value="running" className="h-6 gap-1.5 px-2.5 text-xs">
              Running
              <CountPill
                value={visibleRunning.length}
                active={activeTab === "running"}
              />
            </TabsTrigger>
            <TabsTrigger value="finished" className="h-6 gap-1.5 px-2.5 text-xs">
              Finished
              <CountPill
                value={visibleFinished.length}
                active={activeTab === "finished"}
              />
            </TabsTrigger>
          </TabsList>

          <Select value={repo} onValueChange={setRepo}>
            <SelectTrigger
              className="h-8 w-full gap-1.5 text-xs sm:w-[210px]"
              aria-label="Filter reviews by repository"
            >
              <SelectValue placeholder="All repositories" />
            </SelectTrigger>
            <SelectContent align="end">
              <SelectItem value={ALL_REPOS}>All repositories</SelectItem>
              {repoOptions.map((fullName) => (
                <SelectItem key={fullName} value={fullName}>
                  {fullName}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <TabsContent value="running" className="mt-0">
          <RowList
            rows={visibleRunning}
            emptyLabel={`No running reviews${scopeSuffix(repo)}.`}
            onOpenSession={onOpenSession}
          />
        </TabsContent>
        <TabsContent value="finished" className="mt-0">
          <RowList
            rows={visibleFinished}
            emptyLabel={`No finished reviews${scopeSuffix(repo)}.`}
            onOpenSession={onOpenSession}
          />
        </TabsContent>
      </Tabs>
    </section>
  )
}

function scopeSuffix(repo: string): string {
  return repo === ALL_REPOS ? "" : ` in ${repo}`
}

function CountPill({ value, active }: { value: number; active: boolean }) {
  return (
    <span
      className={cn(
        "rounded-full px-1.5 py-px font-mono text-[10px] tabular",
        active
          ? "bg-muted text-foreground"
          : "bg-background/70 text-muted-foreground",
      )}
    >
      {value}
    </span>
  )
}

interface RowListProps {
  rows: ReviewRow[]
  emptyLabel: string
  onOpenSession: (id: string) => void
}

function RowList({ rows, emptyLabel, onOpenSession }: RowListProps) {
  if (rows.length === 0) {
    return (
      <div className="flex items-center justify-center gap-2 rounded-xl border border-dashed border-border bg-card/50 px-4 py-8 text-[12px] text-muted-foreground">
        <Inbox className="size-3.5 shrink-0" />
        <span>{emptyLabel}</span>
      </div>
    )
  }

  return (
    <div className="divide-y divide-border overflow-hidden rounded-xl border border-border bg-card">
      {rows.map((row) => (
        <ReviewRowItem
          key={row.id}
          row={row}
          onOpen={() => onOpenSession(row.id)}
        />
      ))}
    </div>
  )
}

interface ReviewRowItemProps {
  row: ReviewRow
  onOpen: () => void
}

function ReviewRowItem({ row, onOpen }: ReviewRowItemProps) {
  return (
    <div className="flex w-full items-center">
      <button
        type="button"
        onClick={onOpen}
        title={row.name}
        aria-label={`Open session ${row.title}`}
        className="flex min-w-0 flex-1 items-center gap-2.5 px-3 py-1.5 text-left transition-colors hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-ring"
      >
        <span className="min-w-0 flex-1 truncate text-[13px] font-medium text-foreground">
          {row.title}
        </span>
        {row.elapsedMs === undefined ? (
          <span className="hidden shrink-0 whitespace-nowrap text-2xs text-muted-foreground sm:inline">
            {formatRelativeTime(row.timestamp)}
          </span>
        ) : (
          <span className="hidden shrink-0 whitespace-nowrap font-mono text-[10px] tabular text-running sm:inline">
            {formatDuration(row.elapsedMs)}
          </span>
        )}
        <SessionStatusBadge
          status={row.status}
          showDot
          className="shrink-0 px-1.5"
        />
      </button>

      <Popover>
        <PopoverTrigger asChild>
          <button
            type="button"
            aria-label={`Show all ${row.targets.length} ${
              row.targets.length === 1 ? "repository" : "repositories"
            } and branches`}
            className="mr-2 flex shrink-0 items-center gap-1 rounded-md px-1.5 py-1 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
          >
            <RepoCluster targets={row.targets} />
            <ChevronDown className="size-3.5 opacity-70" />
          </button>
        </PopoverTrigger>
        <PopoverContent align="end" className="w-80 p-1.5">
          <RepoBranchList targets={row.targets} />
        </PopoverContent>
      </Popover>
    </div>
  )
}

function RepoCluster({ targets }: { targets: ReviewTarget[] }) {
  const visible = targets.slice(0, MAX_VISIBLE_REPOS)
  const overflow = targets.length - visible.length

  return (
    <span className="flex items-center">
      <span className="flex -space-x-1.5">
        {visible.map((target) => (
          <RepositoryMark
            key={target.id}
            fullName={target.fullName}
            private={target.private}
            size="sm"
          />
        ))}
      </span>
      {overflow > 0 ? (
        <span className="ml-1.5 font-mono text-[10px] tabular text-muted-foreground">
          +{overflow}
        </span>
      ) : null}
    </span>
  )
}

function RepoBranchList({ targets }: { targets: ReviewTarget[] }) {
  return (
    <div className="flex flex-col gap-0.5">
      <p className="px-2 py-1 text-2xs font-semibold uppercase tracking-widest text-muted-foreground">
        {targets.length} {targets.length === 1 ? "repository" : "repositories"}
      </p>
      {targets.map((target) => (
        <div
          key={target.id}
          className="flex items-center gap-2 rounded-md px-2 py-1.5"
        >
          <RepositoryMark
            fullName={target.fullName}
            private={target.private}
            size="sm"
          />
          <span className="flex min-w-0 flex-col">
            <span className="truncate font-mono text-[11px] text-foreground">
              {target.fullName}
            </span>
            <span className="truncate font-mono text-[10px] text-muted-foreground">
              {target.headBranch}
            </span>
          </span>
        </div>
      ))}
    </div>
  )
}
