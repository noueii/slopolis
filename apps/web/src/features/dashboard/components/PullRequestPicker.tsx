import { useEffect, useMemo, useState } from "react"
import {
  CheckCircle2,
  CircleDashed,
  ExternalLink,
  Search,
  XCircle,
} from "lucide-react"

import { cn } from "@/lib/utils"
import type {
  OpenPullRequest,
  PullRequestChecks,
  RepositorySummary,
} from "@/api/contract"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Skeleton } from "@/components/ui/skeleton"
import { formatRelativeTime } from "@/features/sessions/lib/format"
import { useRepositoryPullRequests } from "../lib/useDashboard"
import { selectionKey, type SelectedPr } from "../lib/selection"
import { RepositoryMark } from "./RepositoryMark"

export interface PullRequestPickerProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  repositories: RepositorySummary[] | null
  repositoriesLoading: boolean
  repositoriesError: string | null
  onRetryRepositories: () => void
  selected: SelectedPr[]
  onToggle: (pr: OpenPullRequest) => void
  onClear: () => void
}

export function PullRequestPicker({
  open,
  onOpenChange,
  repositories,
  repositoriesLoading,
  repositoriesError,
  onRetryRepositories,
  selected,
  onToggle,
  onClear,
}: PullRequestPickerProps) {
  const [focusRepo, setFocusRepo] = useState<string | null>(null)
  const [query, setQuery] = useState("")

  const selectedKeys = useMemo(
    () => new Set(selected.map((item) => selectionKey(item))),
    [selected],
  )

  useEffect(() => {
    if (!open) return
    setQuery("")
    setFocusRepo(
      (prev) =>
        prev ??
        selected[selected.length - 1]?.repository.fullName ??
        repositories?.[0]?.fullName ??
        null,
    )
    // Only re-run when the dialog opens or the repository list first arrives.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, repositories])

  const needle = query.trim().toLowerCase()
  const visibleRepos = (repositories ?? []).filter((repo) =>
    repo.fullName.toLowerCase().includes(needle),
  )

  const pullRequests = useRepositoryPullRequests(open ? focusRepo : null)
  const focusSummary =
    repositories?.find((repo) => repo.fullName === focusRepo) ?? null

  const countForRepo = (fullName: string) =>
    selected.filter((item) => item.repository.fullName === fullName).length

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex h-[min(680px,88vh)] max-w-5xl flex-col gap-0 overflow-hidden p-0">
        <DialogHeader className="border-b border-border px-5 py-4 pr-12">
          <DialogTitle className="text-base">Choose pull requests</DialogTitle>
          <DialogDescription className="text-[13px]">
            Select any number of open pull requests across your connected
            repositories. Cross-repo reviews are supported.
          </DialogDescription>
        </DialogHeader>

        <div className="grid min-h-0 flex-1 grid-cols-1 grid-rows-[auto_1fr] md:grid-cols-[268px_1fr] md:grid-rows-1">
          <aside className="flex max-h-[190px] min-h-0 flex-col border-b border-border md:max-h-none md:border-b-0 md:border-r">
            <div className="relative px-3 py-2.5">
              <Search className="pointer-events-none absolute left-6 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
              <Input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Search repositories…"
                aria-label="Search repositories"
                className="h-8 pl-8 text-[13px]"
              />
            </div>

            <ScrollArea className="min-h-0 flex-1">
              <div className="flex flex-col gap-0.5 px-2 pb-3">
                {repositoriesLoading && !repositories ? (
                  <RepoListSkeleton />
                ) : null}

                {repositoriesError && !repositories ? (
                  <div className="flex flex-col items-start gap-2 px-2 py-3">
                    <p className="text-[12px] text-destructive">
                      {repositoriesError}
                    </p>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={onRetryRepositories}
                    >
                      Try again
                    </Button>
                  </div>
                ) : null}

                {visibleRepos.map((repo) => {
                  const isFocused = repo.fullName === focusRepo
                  const count = countForRepo(repo.fullName)
                  return (
                    <button
                      key={repo.id}
                      type="button"
                      onClick={() => setFocusRepo(repo.fullName)}
                      aria-pressed={isFocused}
                      className={cn(
                        "flex w-full items-center gap-2.5 rounded-md px-2 py-2 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring",
                        isFocused
                          ? "bg-secondary text-foreground"
                          : "text-muted-foreground hover:bg-muted/50 hover:text-foreground",
                      )}
                    >
                      <RepositoryMark
                        fullName={repo.fullName}
                        private={repo.private}
                        size="sm"
                      />
                      <span className="flex min-w-0 flex-1 flex-col">
                        <span className="truncate text-[12px] font-medium text-foreground">
                          {repo.fullName}
                        </span>
                        <span className="font-mono text-[10px] text-muted-foreground">
                          {repo.openPrCount} open
                        </span>
                      </span>
                      {count > 0 ? (
                        <span className="shrink-0 rounded-full bg-accent/12 px-1.5 py-0.5 font-mono text-[10px] font-semibold text-accent">
                          {count}
                        </span>
                      ) : null}
                    </button>
                  )
                })}

                {!repositoriesLoading &&
                !repositoriesError &&
                repositories &&
                visibleRepos.length === 0 ? (
                  <p className="px-2 py-6 text-center text-[12px] text-muted-foreground">
                    {repositories.length === 0
                      ? "No repositories connected."
                      : "No repositories match."}
                  </p>
                ) : null}
              </div>
            </ScrollArea>
          </aside>

          <section className="flex min-h-0 flex-col">
            {focusSummary ? (
              <header className="flex items-center gap-3 border-b border-border px-4 py-3">
                <RepositoryMark
                  fullName={focusSummary.fullName}
                  private={focusSummary.private}
                  size="md"
                />
                <div className="flex min-w-0 flex-1 flex-col">
                  <span className="truncate font-mono text-[13px] font-medium text-foreground">
                    {focusSummary.fullName}
                  </span>
                  <span className="text-2xs text-muted-foreground">
                    {focusSummary.openPrCount} open · default{" "}
                    {focusSummary.defaultBranch}
                  </span>
                </div>
                <a
                  href={`https://github.com/${focusSummary.fullName}`}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-2xs font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                >
                  Repository
                  <ExternalLink className="size-3" />
                </a>
              </header>
            ) : null}

            <ScrollArea className="min-h-0 flex-1">
              <div className="divide-y divide-border">
                {pullRequests.status === "loading" ? <PrListSkeleton /> : null}

                {pullRequests.status === "error" ? (
                  <div className="flex flex-col items-start gap-2 px-4 py-6">
                    <p className="text-[13px] text-destructive">
                      {pullRequests.error ??
                        "Could not load open pull requests."}
                    </p>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={pullRequests.refetch}
                    >
                      Try again
                    </Button>
                  </div>
                ) : null}

                {pullRequests.status === "success" &&
                pullRequests.data?.pullRequests.length === 0 ? (
                  <div className="px-4 py-10 text-center">
                    <p className="text-[13px] font-medium text-foreground">
                      No open pull requests
                    </p>
                    <p className="mt-1 text-[12px] text-muted-foreground">
                      Every PR in {focusRepo ?? "this repository"} is closed or
                      merged.
                    </p>
                  </div>
                ) : null}

                {pullRequests.data?.pullRequests.map((pr) => (
                  <PullRequestRow
                    key={pr.id}
                    pr={pr}
                    selected={selectedKeys.has(selectionKey(pr))}
                    onToggle={() => onToggle(pr)}
                  />
                ))}
              </div>
            </ScrollArea>
          </section>
        </div>

        <DialogFooter className="flex-row items-center justify-between gap-3 border-t border-border bg-muted/30 px-5 py-3 sm:justify-between">
          <div className="flex items-center gap-3">
            <span className="text-[13px] text-muted-foreground">
              <span className="tabular font-mono font-semibold text-foreground">
                {selected.length}
              </span>{" "}
              selected
            </span>
            {selected.length > 0 ? (
              <Button
                variant="ghost"
                size="sm"
                className="h-7 px-2 text-[13px] text-muted-foreground"
                onClick={onClear}
              >
                Clear
              </Button>
            ) : null}
          </div>
          <Button size="sm" onClick={() => onOpenChange(false)}>
            Done
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

interface PullRequestRowProps {
  pr: OpenPullRequest
  selected: boolean
  onToggle: () => void
}

function PullRequestRow({ pr, selected, onToggle }: PullRequestRowProps) {
  return (
    <div
      onClick={onToggle}
      className={cn(
        "flex cursor-pointer items-start gap-3 px-4 py-3 transition-colors",
        selected ? "bg-accent/[0.05]" : "hover:bg-muted/40",
      )}
    >
      <Checkbox
        checked={selected}
        onCheckedChange={onToggle}
        onClick={(event) => event.stopPropagation()}
        aria-label={`Select #${pr.number} ${pr.title}`}
        className="mt-0.5"
      />

      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <div className="flex items-center gap-2">
          <span className="font-mono text-[11px] text-muted-foreground">
            #{pr.number}
          </span>
          {pr.draft ? (
            <Badge
              variant="outline"
              className="h-4 rounded px-1 text-[9px] font-semibold uppercase tracking-wider text-muted-foreground"
            >
              Draft
            </Badge>
          ) : null}
        </div>

        <a
          href={pr.url}
          target="_blank"
          rel="noreferrer"
          onClick={(event) => event.stopPropagation()}
          className="group/link inline-flex items-start gap-1.5 text-left text-[13px] font-medium leading-snug text-foreground hover:text-accent"
        >
          <span className="line-clamp-2">{pr.title}</span>
          <ExternalLink className="mt-0.5 size-3 shrink-0 text-muted-foreground/60 group-hover/link:text-accent" />
        </a>

        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-2xs text-muted-foreground">
          <span>@{pr.author.handle}</span>
          <span>updated {formatRelativeTime(pr.updatedAt)}</span>
          <span className="hidden sm:inline">
            {pr.changedFiles} file{pr.changedFiles === 1 ? "" : "s"}
          </span>
          <span className="hidden items-center gap-1 font-mono sm:inline-flex">
            <span className="text-success">+{pr.additions}</span>
            <span className="text-destructive">−{pr.deletions}</span>
          </span>
          <ChecksHint checks={pr.checks} />
        </div>
      </div>
    </div>
  )
}

function ChecksHint({ checks }: { checks: PullRequestChecks }) {
  if (checks.state === "none") {
    return (
      <span className="inline-flex items-center gap-1">
        <CircleDashed className="size-3" />
        no checks
      </span>
    )
  }
  if (checks.state === "failing") {
    return (
      <span className="inline-flex items-center gap-1 text-destructive">
        <XCircle className="size-3" />
        checks failing
      </span>
    )
  }
  if (checks.state === "pending") {
    return (
      <span className="inline-flex items-center gap-1 text-warning">
        <CircleDashed className="size-3" />
        {checks.passing}/{checks.total} checks
      </span>
    )
  }
  return (
    <span className="inline-flex items-center gap-1 text-success">
      <CheckCircle2 className="size-3" />
      {checks.passing}/{checks.total} checks
    </span>
  )
}

function RepoListSkeleton() {
  return (
    <div className="flex flex-col gap-0.5 px-1 py-1">
      {Array.from({ length: 6 }).map((_, index) => (
        <div key={index} className="flex items-center gap-2.5 px-2 py-2">
          <Skeleton className="size-6 rounded" />
          <div className="flex flex-1 flex-col gap-1.5">
            <Skeleton className="h-3 w-3/4" />
            <Skeleton className="h-2.5 w-1/3" />
          </div>
        </div>
      ))}
    </div>
  )
}

function PrListSkeleton() {
  return (
    <div>
      {Array.from({ length: 7 }).map((_, index) => (
        <div key={index} className="flex items-start gap-3 px-4 py-3">
          <Skeleton className="mt-0.5 size-4 rounded-sm" />
          <div className="flex flex-1 flex-col gap-2">
            <Skeleton className="h-3 w-4/5" />
            <Skeleton className="h-2.5 w-2/5" />
          </div>
        </div>
      ))}
    </div>
  )
}
