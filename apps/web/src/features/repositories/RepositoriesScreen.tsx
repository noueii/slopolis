import { useState } from "react"
import {
  BookMarked,
  ChevronRight,
  Globe,
  Info,
  Lock,
  Plus,
  RefreshCw,
} from "lucide-react"

import { cn } from "@/lib/utils"
import { githubAppInstallUrl } from "@/api/client"
import type { RepositorySummary } from "@/api/contract"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { useRepositories } from "@/features/dashboard/lib/useDashboard"
import { formatRelativeTime } from "@/features/sessions/lib/format"
import { RepositoryAccessDialog } from "./components/RepositoryAccessDialog"
import { useRepositoryAccess } from "./lib/useRepositoryAccess"

export interface RepositoriesScreenProps {
  onOpenRepository: (fullName: string) => void
}

export function RepositoriesScreen({ onOpenRepository }: RepositoriesScreenProps) {
  const { data, status, error, refetch } = useRepositories()
  const repositories = data?.items ?? []
  const loading = status === "loading" && !data
  const failed = status === "error"
  const [confirming, setConfirming] = useState<RepositorySummary | null>(null)
  const access = useRepositoryAccess()

  /** Enabling needs no confirmation; it reopens something the workspace chose. */
  async function toggle(repository: RepositorySummary) {
    if (!repository.enabled) {
      const updated = await access.run(repository, true)
      if (updated !== null) refetch()
      return
    }
    setConfirming(repository)
  }

  async function confirmDisable() {
    if (confirming === null) return
    const updated = await access.run(confirming, false)
    if (updated === null) return
    setConfirming(null)
    refetch()
  }

  return (
    <div className="mx-auto flex w-full max-w-[1180px] animate-fade-up flex-col gap-6 p-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div className="flex flex-col gap-1">
          <p className="font-mono text-2xs uppercase tracking-widest text-muted-foreground">
            Workspace
          </p>
          <h1 className="text-xl font-semibold tracking-tight">Repositories</h1>
          <p className="max-w-2xl text-sm text-muted-foreground">
            slopolis reviews pull requests from the repositories covered by your
            GitHub App installation.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={refetch}>
            <RefreshCw
              data-icon="inline-start"
              className={cn(status === "loading" && "animate-spin")}
            />
            Refresh
          </Button>
          <Button size="sm" asChild>
            <a href={githubAppInstallUrl}>
              <Plus data-icon="inline-start" />
              Connect repository
            </a>
          </Button>
        </div>
      </header>

      <div className="flex items-start gap-2.5 rounded-lg border border-dashed border-border bg-muted/30 px-4 py-3">
        <Info className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
        <p className="text-[13px] leading-relaxed text-muted-foreground">
          Adding a repository happens on GitHub while installing or updating the
          slopolis GitHub App — you choose which accounts and repositories to
          grant access to. There is no in-app add-repository endpoint yet, so
          this action opens the GitHub App install flow. Which of the granted
          repositories slopolis reviews is this workspace&apos;s own switch:
          disable one to park it.
        </p>
      </div>

      {access.error !== null && confirming === null ? (
        <p role="alert" className="text-xs leading-relaxed text-destructive">
          {access.error}
        </p>
      ) : null}

      {loading ? <RepositoryListSkeleton /> : null}

      {failed ? (
        <div className="flex flex-col items-start gap-3 rounded-lg border border-destructive/30 bg-destructive/5 px-6 py-8">
          <h2 className="text-sm font-semibold tracking-tight text-destructive">
            Could not load repositories
          </h2>
          <p className="max-w-xl text-sm text-muted-foreground">
            {error ?? "Something went wrong while loading repositories."}
          </p>
          <Button variant="outline" size="sm" onClick={refetch}>
            <RefreshCw data-icon="inline-start" />
            Try again
          </Button>
        </div>
      ) : null}

      {!loading && !failed && repositories.length === 0 ? (
        <div className="flex flex-col items-center gap-4 rounded-xl border border-border bg-card px-6 py-16 text-center">
          <span className="grid size-12 place-items-center rounded-full border border-border bg-muted text-muted-foreground">
            <BookMarked className="size-5" />
          </span>
          <div className="flex flex-col gap-1">
            <h2 className="text-base font-semibold tracking-tight">
              No repositories connected yet
            </h2>
            <p className="max-w-md text-sm text-muted-foreground">
              Install the slopolis GitHub App and grant access to the
              repositories you want reviewed.
            </p>
          </div>
          <Button size="sm" asChild>
            <a href={githubAppInstallUrl}>
              <Plus data-icon="inline-start" />
              Connect repository
            </a>
          </Button>
        </div>
      ) : null}

      {!loading && !failed && repositories.length > 0 ? (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {repositories.map((repository) => (
            <RepositoryCard
              key={repository.id}
              repository={repository}
              onOpen={() => onOpenRepository(repository.fullName)}
              onToggle={() => void toggle(repository)}
            />
          ))}
        </div>
      ) : null}

      {confirming !== null ? (
        <RepositoryAccessDialog
          repository={confirming}
          pending={access.pending}
          error={access.error}
          onOpenChange={() => setConfirming(null)}
          onConfirm={() => void confirmDisable()}
        />
      ) : null}
    </div>
  )
}

interface RepositoryCardProps {
  repository: RepositorySummary
  onOpen: () => void
  onToggle: () => void
}

function RepositoryCard({ repository, onOpen, onToggle }: RepositoryCardProps) {
  const VisibilityIcon = repository.private ? Lock : Globe

  return (
    <div
      className={cn(
        "group flex w-full flex-col gap-3 rounded-xl border bg-card px-4 py-3.5 transition-colors",
        repository.enabled
          ? "border-border hover:border-accent/50 hover:bg-muted/30"
          : "border-dashed border-border bg-muted/20",
      )}
    >
      <button
        type="button"
        onClick={onOpen}
        aria-label={`Open repository ${repository.fullName}`}
        className="flex w-full flex-col gap-3 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <div className="flex items-start gap-2.5">
          <span className="grid size-8 shrink-0 place-items-center rounded-lg border border-border bg-muted/40 text-muted-foreground">
            <BookMarked className="size-4" />
          </span>
          <div className="flex min-w-0 flex-1 flex-col">
            <span className="truncate font-mono text-[12px] font-medium text-foreground">
              {repository.fullName}
            </span>
            <span className="flex items-center gap-1.5 text-2xs text-muted-foreground">
              <VisibilityIcon className="size-3" />
              {repository.private ? "Private" : "Public"}
              <span className="text-muted-foreground/50">·</span>
              <span className="font-mono">{repository.defaultBranch}</span>
            </span>
          </div>
          <ChevronRight className="mt-0.5 size-4 shrink-0 text-muted-foreground/40 transition-colors group-hover:text-muted-foreground" />
        </div>

        <div className="flex items-center gap-3 text-2xs text-muted-foreground">
          <span className="font-mono">
            {repository.openPrCount} open PR
            {repository.openPrCount === 1 ? "" : "s"}
          </span>
          <span className="text-muted-foreground/50">·</span>
          <span>{formatRelativeTime(repository.lastActivityAt)}</span>
        </div>
      </button>

      <div className="flex items-center justify-between gap-2 border-t border-border pt-3">
        <span className="flex items-center gap-1.5">
          <ConnectionBadge connected={repository.connected} />
          {repository.enabled ? null : <AccessBadge />}
        </span>
        <Button
          type="button"
          variant={repository.enabled ? "outline" : "default"}
          size="sm"
          onClick={onToggle}
          className={cn(
            repository.enabled &&
              "text-muted-foreground hover:text-destructive",
          )}
        >
          {repository.enabled ? "Disable" : "Enable"}
        </Button>
      </div>
    </div>
  )
}

/** Shown beside the connection badge when the workspace has parked the row. */
export function AccessBadge() {
  return (
    <span className="rounded-full border border-warning/30 bg-warning/10 px-2 py-0.5 font-mono text-[10px] uppercase tracking-wider text-warning">
      Disabled
    </span>
  )
}

export function ConnectionBadge({ connected }: { connected: boolean }) {
  return (
    <span
      className={cn(
        "rounded-full border px-2 py-0.5 font-mono text-[10px] uppercase tracking-wider",
        connected
          ? "border-success/30 bg-success/10 text-success"
          : "border-destructive/30 bg-destructive/10 text-destructive",
      )}
    >
      {connected ? "Connected" : "Disconnected"}
    </span>
  )
}

function RepositoryListSkeleton() {
  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3" aria-hidden>
      {Array.from({ length: 6 }).map((_, index) => (
        <div
          key={index}
          className="flex flex-col gap-3 rounded-xl border border-border bg-card px-4 py-3.5"
        >
          <div className="flex items-center gap-2.5">
            <Skeleton className="size-8 shrink-0 rounded-lg" />
            <div className="flex flex-1 flex-col gap-1.5">
              <Skeleton className="h-3 w-32" />
              <Skeleton className="h-2.5 w-20" />
            </div>
          </div>
          <Skeleton className="h-3 w-full" />
        </div>
      ))}
    </div>
  )
}
