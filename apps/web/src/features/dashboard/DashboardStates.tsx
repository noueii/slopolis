import { AlertTriangle, Inbox, RotateCw } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"

export function DashboardSkeleton() {
  return (
    <div className="flex flex-col gap-5" aria-hidden>
      <div className="flex flex-col gap-3">
        <Skeleton className="h-4 w-28" />
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {[0, 1, 2].map((index) => (
            <Skeleton key={index} className="h-[122px] rounded-lg" />
          ))}
        </div>
      </div>

      <div className="flex flex-col gap-3">
        <Skeleton className="h-4 w-28" />
        <div className="overflow-hidden rounded-xl border border-border">
          {Array.from({ length: 5 }).map((_, index) => (
            <div
              key={index}
              className="flex items-start gap-3.5 border-b border-border px-4 py-3.5 last:border-b-0"
            >
              <Skeleton className="size-7 shrink-0 rounded-full" />
              <div className="flex flex-1 flex-col gap-2">
                <Skeleton className="h-3.5 w-2/5" />
                <Skeleton className="h-3 w-3/5" />
              </div>
              <Skeleton className="h-3 w-16 shrink-0" />
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

export interface DashboardErrorProps {
  message: string
  onRetry: () => void
}

export function DashboardError({ message, onRetry }: DashboardErrorProps) {
  return (
    <div className="flex flex-col items-start gap-3 rounded-lg border border-destructive/30 bg-destructive/5 px-6 py-8">
      <div className="flex items-center gap-2.5">
        <AlertTriangle className="size-4 text-destructive" />
        <h2 className="text-sm font-semibold tracking-tight text-destructive">
          Could not load the dashboard
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

export interface DashboardEmptyProps {
  scoped: boolean
  onNewReview: () => void
}

export function DashboardEmpty({ scoped, onNewReview }: DashboardEmptyProps) {
  return (
    <div className="flex flex-col items-center gap-4 rounded-xl border border-border bg-card px-6 py-16 text-center">
      <span className="grid size-12 place-items-center rounded-full border border-border bg-muted text-muted-foreground">
        <Inbox className="size-5" />
      </span>
      <div className="flex flex-col gap-1">
        <h2 className="text-base font-semibold tracking-tight">
          {scoped ? "No reviews for this repository yet" : "No review sessions yet"}
        </h2>
        <p className="max-w-md text-sm text-muted-foreground">
          {scoped
            ? "Switch back to all repositories, or start the first review against this one."
            : "Paste a few pull request links above and slopolis will run, publish, and track the review here."}
        </p>
      </div>
      <Button size="sm" onClick={onNewReview}>
        Start a review
      </Button>
    </div>
  )
}
