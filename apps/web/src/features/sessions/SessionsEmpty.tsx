import { Inbox, Plus, SearchX } from "lucide-react"

import { Button } from "@/components/ui/button"

export interface SessionsEmptyProps {
  filtered: boolean
  onClear: () => void
  onNewReview: () => void
}

export function SessionsEmpty({
  filtered,
  onClear,
  onNewReview,
}: SessionsEmptyProps) {
  const Icon = filtered ? SearchX : Inbox

  return (
    <div className="flex flex-col items-center gap-4 rounded-lg border border-border bg-card px-6 py-20 text-center">
      <span className="grid size-12 place-items-center rounded-full border border-border bg-muted text-muted-foreground">
        <Icon className="size-5" />
      </span>
      <div className="flex flex-col gap-1">
        <h2 className="text-base font-semibold tracking-tight">
          {filtered ? "No sessions match these filters" : "No review sessions yet"}
        </h2>
        <p className="max-w-md text-sm text-muted-foreground">
          {filtered
            ? "Try widening the date range, clearing the repo or user filter, or adjusting your search."
            : "Submit a few pull requests and slopolis will review them, post findings, and track the run here."}
        </p>
      </div>
      {filtered ? (
        <Button variant="outline" size="sm" onClick={onClear}>
          Clear filters
        </Button>
      ) : (
        <Button size="sm" onClick={onNewReview}>
          <Plus data-icon="inline-start" />
          New review
        </Button>
      )}
    </div>
  )
}
