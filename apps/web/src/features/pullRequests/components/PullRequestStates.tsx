/**
 * The inbox's non-list states (spec v3 §5).
 *
 * Loading keeps the filter bar usable and shows row skeletons, so a filter
 * change never blanks the page. The three empty states are not
 * interchangeable: with nothing connected the only useful action lives in the
 * Repositories screen, an unfiltered empty list genuinely has nothing open,
 * and a filtered-to-nothing list needs its filters cleared.
 */

import { AlertTriangle, FolderGit2, Inbox, RotateCw, SearchX } from "lucide-react"

import type { PullRequestSummary } from "@/api/contract"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"

export interface PullRequestSkeletonProps {
  rows?: number
}

/** Row skeletons, matching the two-line row's height so nothing jumps. */
export function PullRequestSkeleton({ rows = 8 }: PullRequestSkeletonProps) {
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-card">
      {Array.from({ length: rows }, (_, index) => (
        <div
          key={index}
          className="flex items-start gap-3 border-b border-border px-3 py-3 last:border-b-0"
        >
          <Skeleton className="mt-0.5 size-4 rounded-sm" />
          <div className="flex flex-1 flex-col gap-2">
            <Skeleton className="h-3.5 w-[46%]" />
            <Skeleton className="h-3 w-[68%]" />
          </div>
        </div>
      ))}
    </div>
  )
}

export interface PullRequestErrorProps {
  message: string
  onRetry: () => void
}

export function PullRequestError({ message, onRetry }: PullRequestErrorProps) {
  return (
    <div className="flex flex-col items-start gap-3 rounded-lg border border-destructive/30 bg-destructive/5 px-6 py-8">
      <div className="flex items-center gap-2.5">
        <AlertTriangle className="size-4 text-destructive" />
        <h2 className="text-sm font-semibold tracking-tight text-destructive">
          Could not load pull requests
        </h2>
      </div>
      <p className="max-w-xl text-sm text-muted-foreground">{message}</p>
      <Button variant="outline" size="sm" onClick={onRetry}>
        <RotateCw data-icon="inline-start" />
        Try again
      </Button>
    </div>
  )
}

export type PullRequestEmptyKind =
  | "no-repositories"
  | "nothing-open"
  | "no-matches"

/**
 * Which empty state applies. `connectedRepositories` comes from
 * `GET /api/repositories`, because the inbox's own filter options list
 * repositories that have no open pull requests too — only a zero *connection*
 * count means there is nothing to install-and-connect. A `null` count is an
 * unknown workspace, which must not claim a disconnected one. Otherwise an
 * active filter, or a filtered set with rows on another page, means the rows
 * were filtered away rather than absent.
 */
export function emptyKindFor(
  summary: PullRequestSummary,
  connectedRepositories: number | null,
  activeFilters: number,
): PullRequestEmptyKind {
  if (connectedRepositories === 0) return "no-repositories"
  if (summary.total > 0 || activeFilters > 0) return "no-matches"
  return "nothing-open"
}

export interface PullRequestEmptyProps {
  kind: PullRequestEmptyKind
  onClearFilters: () => void
  onOpenRepositories: () => void
}

export function PullRequestEmpty({
  kind,
  onClearFilters,
  onOpenRepositories,
}: PullRequestEmptyProps) {
  const icon =
    kind === "no-repositories" ? (
      <FolderGit2 className="size-5" />
    ) : kind === "no-matches" ? (
      <SearchX className="size-5" />
    ) : (
      <Inbox className="size-5" />
    )

  const copy =
    kind === "no-repositories"
      ? {
          title: "No repositories connected",
          body: "Connect the repositories whose pull requests slopolis should review, then their open pull requests appear here.",
          action: "Go to Repositories",
        }
      : kind === "no-matches"
        ? {
            title: "No pull requests match your filters",
            body: "Try a different repository, review state, or search term.",
            action: "Clear filters",
          }
        : {
            title: "Nothing open",
            body: "Every connected repository is clear — there are no open pull requests to review right now.",
            action: "View repositories",
          }

  return (
    <div className="flex flex-col items-center gap-4 rounded-lg border border-border bg-card px-6 py-20 text-center">
      <span className="grid size-12 place-items-center rounded-full border border-border bg-muted text-muted-foreground">
        {icon}
      </span>
      <div className="flex flex-col gap-1">
        <h2 className="text-base font-semibold tracking-tight">{copy.title}</h2>
        <p className="max-w-md text-sm text-muted-foreground">{copy.body}</p>
      </div>
      <Button
        variant="outline"
        size="sm"
        onClick={kind === "no-matches" ? onClearFilters : onOpenRepositories}
      >
        {copy.action}
      </Button>
    </div>
  )
}
