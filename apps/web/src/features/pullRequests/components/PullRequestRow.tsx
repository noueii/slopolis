/**
 * One inbox row (spec v3 §1).
 *
 * The row — not the title — is the selection surface: clicking anywhere in it
 * toggles the pull request, except on a link or a button, which keep their own
 * job (the GitHub title, the session reference, the checkbox). Shift-click
 * extends from the last row that was toggled.
 */

import type { MouseEvent } from "react"
import {
  CheckCircle2,
  Clock,
  ExternalLink,
  Minus,
  XCircle,
  type LucideIcon,
} from "lucide-react"

import { cn } from "@/lib/utils"
import type {
  PullRequestChecks,
  PullRequestListItem,
} from "@/api/contract"
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar"
import { Badge } from "@/components/ui/badge"
import { Checkbox } from "@/components/ui/checkbox"
import { formatRelativeTime, initials } from "@/features/sessions/lib/format"
import { RepositoryMark } from "./RepositoryMark"
import { ReviewStateBadge } from "./ReviewStateBadge"

export interface PullRequestRowProps {
  pr: PullRequestListItem
  selected: boolean
  onToggle: (pr: PullRequestListItem, options: { range: boolean }) => void
  onOpenSession?: (id: string) => void
}

const CHECKS: Record<
  PullRequestChecks["state"],
  { label: string; icon: LucideIcon; tone: string }
> = {
  passing: {
    label: "Checks passing",
    icon: CheckCircle2,
    tone: "text-success",
  },
  failing: {
    label: "Checks failing",
    icon: XCircle,
    tone: "text-destructive",
  },
  pending: { label: "Checks pending", icon: Clock, tone: "text-info" },
  none: { label: "No checks", icon: Minus, tone: "text-muted-foreground/70" },
}

/** Separator between the row's inline facts. */
function Sep() {
  return (
    <span className="text-muted-foreground/40" aria-hidden>
      ·
    </span>
  )
}

function ChecksRollup({ checks }: { checks: PullRequestChecks }) {
  const { label, icon: Icon, tone } = CHECKS[checks.state]
  return (
    <span className="inline-flex items-center gap-1" title={label}>
      <Icon className={cn("size-3", tone)} aria-hidden />
      <span className={cn(tone)}>
        {checks.total > 0 ? `${checks.passing}/${checks.total}` : label}
      </span>
    </span>
  )
}

export function PullRequestRow({
  pr,
  selected,
  onToggle,
  onOpenSession,
}: PullRequestRowProps) {
  const fullName = pr.repository.fullName
  const sessionId = pr.review.sessionId

  const handleClick = (event: MouseEvent<HTMLLIElement>) => {
    if (
      (event.target as HTMLElement).closest("a, button, [role='checkbox']")
    ) {
      return
    }
    onToggle(pr, { range: event.shiftKey })
  }

  return (
    <li
      onClick={handleClick}
      className={cn(
        "group flex cursor-pointer items-start gap-3 px-3 py-2.5 transition-colors hover:bg-muted/40",
        selected && "bg-accent/40",
      )}
    >
      <span
        className={cn(
          "pt-0.5 transition-opacity",
          selected
            ? "opacity-100"
            : "opacity-0 group-hover:opacity-100 focus-within:opacity-100",
        )}
      >
        <Checkbox
          checked={selected}
          onCheckedChange={() => onToggle(pr, { range: false })}
          aria-label={`Select ${fullName} #${pr.number}`}
        />
      </span>

      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <div className="flex min-w-0 items-center gap-2">
          <a
            href={pr.url}
            target="_blank"
            rel="noreferrer"
            className="truncate text-[13px] font-medium text-foreground hover:text-accent hover:underline"
          >
            {pr.title}
          </a>
          <ExternalLink
            className="size-3 shrink-0 text-muted-foreground/50"
            aria-hidden
          />
          {pr.draft ? (
            <Badge
              variant="outline"
              className="shrink-0 border-border bg-muted/60 text-2xs font-semibold uppercase tracking-wider text-muted-foreground"
            >
              Draft
            </Badge>
          ) : null}
        </div>

        <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-2xs text-muted-foreground">
          <RepositoryMark
            fullName={fullName}
            private={pr.repository.private}
            size="sm"
          />
          <span className="font-mono text-2xs text-foreground/75">
            {fullName} <span className="text-muted-foreground">#{pr.number}</span>
          </span>

          <Sep />
          <ReviewStateBadge review={pr.review} />

          <Sep />
          <span className="tabular font-mono">
            <span className="text-success">+{pr.additions}</span>{" "}
            <span className="text-destructive">−{pr.deletions}</span>
          </span>

          <Sep />
          <span className="tabular font-mono">
            {pr.changedFiles} file{pr.changedFiles === 1 ? "" : "s"}
          </span>

          <Sep />
          <ChecksRollup checks={pr.checks} />

          <Sep />
          <span className="inline-flex items-center gap-1.5">
            <Avatar className="size-4">
              {pr.author.avatarUrl ? (
                <AvatarImage src={pr.author.avatarUrl} alt="" />
              ) : null}
              <AvatarFallback className="text-[8px]">
                {initials(pr.author.name)}
              </AvatarFallback>
            </Avatar>
            <span>{pr.author.handle}</span>
          </span>

          <Sep />
          <span>{formatRelativeTime(pr.updatedAt)}</span>

          {sessionId ? (
            <>
              <Sep />
              <button
                type="button"
                onClick={() => onOpenSession?.(sessionId)}
                className="font-medium text-accent hover:underline"
              >
                Session
              </button>
            </>
          ) : null}
        </div>
      </div>
    </li>
  )
}
