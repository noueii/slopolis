import { useMemo, useState } from "react"
import { ArrowLeft, Globe, Inbox, Lock, RefreshCw } from "lucide-react"

import { cn } from "@/lib/utils"
import type { SessionSort } from "@/api/contract"
import { Button } from "@/components/ui/button"
import { useRepositories } from "@/features/dashboard/lib/useDashboard"
import { SessionsError } from "@/features/sessions/SessionsError"
import { SessionsSkeleton } from "@/features/sessions/SessionsSkeleton"
import { SessionsTable } from "@/features/sessions/SessionsTable"
import { SessionsTableCard } from "@/features/sessions/components/SessionsTableCard"
import { useSessions } from "@/features/sessions/lib/useSessions"
import { ConnectionBadge } from "./RepositoriesScreen"

export interface RepositoryDetailScreenProps {
  fullName: string
  onBack: () => void
  onOpenSession: (id: string) => void
}

const PAGE_SIZE = 50

export function RepositoryDetailScreen({
  fullName,
  onBack,
  onOpenSession,
}: RepositoryDetailScreenProps) {
  const [sort, setSort] = useState<SessionSort>("created_desc")
  const params = useMemo(
    () => ({ repo: fullName, pageSize: PAGE_SIZE, sort }),
    [fullName, sort],
  )

  const { data, status, error, refetch } = useSessions(params)
  const { data: repositories } = useRepositories()
  const repository =
    repositories?.items.find((item) => item.fullName === fullName) ?? null
  const VisibilityIcon = repository?.private ? Lock : Globe

  const sessions = data?.items ?? []
  const busy = status === "loading"
  const showSkeleton = status === "loading" && !data
  const showError = status === "error"
  const showEmpty = !showError && data !== null && sessions.length === 0
  const showTable = !showError && data !== null && sessions.length > 0

  return (
    <div
      className="mx-auto flex w-full max-w-[1180px] animate-fade-up flex-col gap-6 p-6"
      aria-busy={busy}
    >
      <div className="flex flex-col gap-3">
        <Button
          variant="ghost"
          size="sm"
          className="-ml-2 w-fit gap-1.5 text-muted-foreground"
          onClick={onBack}
        >
          <ArrowLeft data-icon="inline-start" />
          Repositories
        </Button>

        <header className="flex flex-wrap items-end justify-between gap-3">
          <div className="flex min-w-0 flex-col gap-1">
            <p className="font-mono text-2xs uppercase tracking-widest text-muted-foreground">
              slopolis / Repositories / {fullName}
            </p>
            <h1 className="truncate text-xl font-semibold tracking-tight">
              {fullName}
            </h1>
            <div className="flex flex-wrap items-center gap-2 text-2xs text-muted-foreground">
              {repository ? (
                <>
                  <span className="flex items-center gap-1.5">
                    <VisibilityIcon className="size-3" />
                    {repository.private ? "Private" : "Public"}
                  </span>
                  <span className="text-muted-foreground/50">·</span>
                  <span className="font-mono">{repository.defaultBranch}</span>
                  <ConnectionBadge connected={repository.connected} />
                </>
              ) : (
                <span>Repository details unavailable</span>
              )}
            </div>
          </div>
          <Button variant="outline" size="sm" onClick={refetch}>
            <RefreshCw
              data-icon="inline-start"
              className={cn(busy && "animate-spin")}
            />
            Refresh
          </Button>
        </header>
      </div>

      {showSkeleton ? <SessionsSkeleton /> : null}

      {showError ? (
        <SessionsError
          message={error ?? "Something went wrong while loading sessions."}
          onRetry={refetch}
        />
      ) : null}

      {showEmpty ? <RepositorySessionsEmpty fullName={fullName} /> : null}

      {showTable ? (
        <SessionsTableCard busy={busy}>
          <SessionsTable
            sessions={sessions}
            sort={sort}
            onSortChange={setSort}
            onOpenSession={onOpenSession}
          />
        </SessionsTableCard>
      ) : null}
    </div>
  )
}

function RepositorySessionsEmpty({ fullName }: { fullName: string }) {
  return (
    <div className="flex flex-col items-center gap-4 rounded-lg border border-border bg-card px-6 py-20 text-center">
      <span className="grid size-12 place-items-center rounded-full border border-border bg-muted text-muted-foreground">
        <Inbox className="size-5" />
      </span>
      <div className="flex flex-col gap-1">
        <h2 className="text-base font-semibold tracking-tight">
          No review sessions for {fullName} yet
        </h2>
        <p className="max-w-md text-sm text-muted-foreground">
          Submit pull requests from this repository and slopolis will review
          them, post findings, and track the run here.
        </p>
      </div>
    </div>
  )
}
