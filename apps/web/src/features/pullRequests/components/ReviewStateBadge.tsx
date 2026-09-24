/**
 * The inbox's review-state column (spec v3 §2).
 *
 * Staleness is orthogonal to the state, which is why it lives here: a
 * `reviewed` row whose review covered an older head must never render as a
 * plain success badge — the commit distance *is* the badge. In-flight rows
 * carry their progress and current step, because "running" on its own says
 * nothing about whether the review is nearly done, and `queued` has no
 * progress to show yet.
 */

import { Fragment, type ReactNode } from "react"

import { cn } from "@/lib/utils"
import type { PullRequestReview, PullRequestReviewState } from "@/api/contract"

const PILL =
  "inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-2xs font-semibold"

type Tone = PullRequestReviewState | "stale"

const TONES: Record<Tone, string> = {
  never: "border-border/80 bg-muted/70 text-muted-foreground",
  queued: "border-border/80 bg-muted/70 text-muted-foreground",
  running: "border-info/30 bg-info/10 text-info",
  reviewed: "border-success/30 bg-success/10 text-success",
  stale: "border-warning/30 bg-warning/10 text-warning",
  failed: "border-destructive/30 bg-destructive/10 text-destructive",
}

const DOTS: Record<Tone, string> = {
  never: "bg-muted-foreground/50",
  queued: "bg-muted-foreground/60",
  running: "bg-info animate-pulse-ring",
  reviewed: "bg-success",
  stale: "bg-warning",
  failed: "bg-destructive",
}

const LABELS: Record<PullRequestReviewState, string> = {
  never: "Not reviewed",
  queued: "Queued",
  running: "Running",
  reviewed: "Reviewed",
  failed: "Failed",
}

export interface ReviewStateBadgeProps {
  review: PullRequestReview
  className?: string
}

export function ReviewStateBadge({ review, className }: ReviewStateBadgeProps) {
  const { commitsSinceReview } = review
  // A review that predates commit tracking cannot be called current: the app
  // knows it happened and nothing about the commit it read.
  const untracked = review.state === "reviewed" && review.reviewedSha === null
  const stale =
    review.state === "reviewed" &&
    !untracked &&
    (commitsSinceReview === null || commitsSinceReview > 0)
  const inFlight = review.state === "queued" || review.state === "running"
  const tone: Tone = stale ? "stale" : review.state

  // A stale review is never badged as reviewed: the commit distance replaces
  // the state word, which is what makes the row worth selecting again.
  const parts: ReactNode[] = stale
    ? [
        commitsSinceReview === null
          ? "New commits since review"
          : `${commitsSinceReview} commit${
              commitsSinceReview === 1 ? "" : "s"
            } behind`,
      ]
    : untracked
      ? [LABELS[review.state], "commit not recorded"]
      : [LABELS[review.state]]
  if (inFlight) {
    if (review.progress !== null) parts.push(`${Math.round(review.progress)}%`)
    if (review.step) parts.push(review.step)
  }
  if (review.state === "reviewed" && !stale && review.findingsCount > 0) {
    parts.push(
      `${review.findingsCount} finding${review.findingsCount === 1 ? "" : "s"}`,
    )
  }

  return (
    <span className={cn(PILL, TONES[tone], className)}>
      <span
        className={cn("size-1.5 shrink-0 rounded-full", DOTS[tone])}
        aria-hidden
      />
      {parts.map((part, index) => (
        <Fragment key={index}>
          {index > 0 ? (
            <span className="text-muted-foreground/50" aria-hidden>
              ·
            </span>
          ) : null}
          <span className="whitespace-nowrap">{part}</span>
        </Fragment>
      ))}
    </span>
  )
}
