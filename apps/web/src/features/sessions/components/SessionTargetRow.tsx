import { useState } from "react"
import { ChevronDown, ExternalLink } from "lucide-react"

import type { Finding, SessionTarget, Severity } from "@/api/contract"
import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible"
import { cn } from "@/lib/utils"
import { parseDiffHunk, type DiffHunkLine, type DiffHunkLineKind } from "../lib/diffHunk"
import {
  formatCost,
  formatDuration,
  formatRelativeTime,
  formatTokens,
  initials,
} from "../lib/format"
import { SessionStatusBadge } from "./SessionStatusBadge"

/**
 * The palette `RunNodeDetail` reads run events in, reused so a finding's
 * severity looks like every other severity in the session. `error` and
 * `critical` share the destructive pair: both block a merge, and the chip's
 * label is what separates them.
 */
const SEVERITY_STYLES: Record<Severity, string> = {
  info: "border-info/30 bg-info/10 text-info",
  warning: "border-warning/30 bg-warning/10 text-warning",
  error: "border-destructive/30 bg-destructive/10 text-destructive",
  critical: "border-destructive/30 bg-destructive/10 text-destructive",
}

const SEVERITY_LABELS: Record<Severity, string> = {
  info: "Info",
  warning: "Warning",
  error: "Error",
  critical: "Critical",
}

/**
 * Why a finding has no inline comment to point at. The reader sees this on
 * hover, so it says where the finding went instead of only what is missing.
 */
const NOT_POSTED_HINT =
  "No inline comment on the pull request: a finding without a diff line, or below the repository's severity threshold, goes into the summary comment instead — and a refused publish leaves every finding unposted."

/** The marker column of a hunk row, as GitHub prints it. */
const DIFF_MARKERS: Record<DiffHunkLineKind, string> = {
  context: "",
  added: "+",
  removed: "-",
}

/** The inline comment a finding left, in the shape the card renders. */
interface PostedComment {
  url: string
  /** Full login the comment is posted as, e.g. `slopolis-dev[bot]`. */
  login: string
  /** That login without GitHub's `[bot]` suffix — the name a reader knows. */
  name: string
  postedAt: string
  /** The hunk GitHub printed with the comment, or `null` when it sent none. */
  diffHunk: string | null
}

/**
 * The comment a finding left on the pull request, or `null` when it never
 * posted one. The wire derives all three fields from the finding row's single
 * `posted` flag, so a finding with a `commentUrl` has an author and a time
 * too; this only narrows the nullable fields into the shape the card reads.
 */
function postedComment(finding: Finding): PostedComment | null {
  const { commentUrl, author, postedAt } = finding
  if (commentUrl === null || author === null || postedAt === null) return null
  return {
    url: commentUrl,
    login: author,
    name: author.endsWith("[bot]") ? author.slice(0, -"[bot]".length) : author,
    postedAt,
    diffHunk: finding.diffHunk,
  }
}

/**
 * A finding's message with its backticked identifiers rendered as code — the
 * reviewer writes one or two sentences and backticks the names in them
 * (spec 10.6).
 *
 * Deliberately not a markdown engine: nothing in the harness asks the model
 * for markdown, and a message that carried any would then show it literally
 * rather than half-rendered. Line breaks are kept as written.
 */
function MessageBody({ message }: { message: string }) {
  // `split` alternates literal and backticked segments, always starting with a
  // literal one, so the odd positions are the identifiers.
  return (
    <p className="whitespace-pre-wrap text-[13px] leading-relaxed text-foreground/90">
      {message.split("`").map((part, index) =>
        index % 2 === 1 ? (
          <code
            key={index}
            className="rounded bg-muted px-1 py-px font-mono text-xs"
          >
            {part}
          </code>
        ) : (
          part
        ),
      )}
    </p>
  )
}

/**
 * The finding's literal replacement code (spec 10.7), shown the way GitHub
 * shows a suggestion: a block labelled `suggestion`, so the reader can tell
 * the code apart from the prose above it.
 */
function SuggestionBlock({ code }: { code: string }) {
  return (
    <div className="border-t border-border bg-muted/30">
      <div className="px-3 pt-1.5 text-2xs uppercase tracking-wider text-muted-foreground">
        suggestion
      </div>
      <pre className="overflow-x-auto px-3 pb-2.5 pt-1 font-mono text-xs leading-relaxed text-foreground/90">
        {code}
      </pre>
    </div>
  )
}

/**
 * Hunk rows, rendered the way GitHub renders them: both line-number gutters,
 * the change marker, and the code — with the hunk's heading when this is the
 * top of it. The row the comment sits on — the one whose *new* line number is
 * the finding's line — carries a left accent.
 *
 * The rows arrive already split around the comment: GitHub prints the comment
 * directly under the line it is on and keeps the rest of the hunk below it, so
 * a comment in the middle of a hunk is a row inside the diff rather than a
 * block after it.
 */
function DiffHunkRows({
  header,
  lines,
  commentedLine,
  tone,
}: {
  header?: string
  lines: DiffHunkLine[]
  commentedLine: number | null
  tone: "top" | "tail"
}) {
  return (
    <div
      className={cn(
        "bg-muted/20 py-1 font-mono text-2xs",
        tone === "top" ? "border-b border-border" : "border-t border-border",
      )}
    >
      {header ? (
        <div className="px-3 py-0.5 text-muted-foreground">{header}</div>
      ) : null}
      {lines.map((line, index) => (
        <div
          key={index}
          className={cn(
            // The accent sits on a transparent border of its own width, so the
            // commented row lines up with every other row.
            "flex gap-3 border-l-2 border-l-transparent px-3 py-px",
            line.kind === "added" && "bg-success/10",
            line.kind === "removed" && "bg-destructive/10",
            commentedLine !== null &&
              line.newLine === commentedLine &&
              "border-l-accent",
          )}
        >
          <span className="w-8 shrink-0 text-right text-muted-foreground/60">
            {line.oldLine}
          </span>
          <span className="w-8 shrink-0 text-right text-muted-foreground/60">
            {line.newLine}
          </span>
          <span className="w-2 shrink-0 text-muted-foreground">
            {DIFF_MARKERS[line.kind]}
          </span>
          <span className="whitespace-pre text-foreground/90">{line.text}</span>
        </div>
      ))}
    </div>
  )
}

/**
 * One finding as a comment card: the file it cites, the hunk the comment is
 * on, the comment it left (or why it left none), the message, and the
 * replacement it suggests.
 */
function FindingRow({ finding }: { finding: Finding }) {
  // The line is what an inline comment anchors to, so a finding without one
  // cites the file alone rather than a `path:null`.
  const location =
    finding.line === null ? finding.path : `${finding.path}:${finding.line}`
  const comment = postedComment(finding)
  // The rows above the comment, the line it sits on, and the rows below it:
  // GitHub prints the comment directly under that line and continues the hunk
  // underneath, so the hunk is split here rather than rendered whole.
  const hunk = comment?.diffHunk ? parseDiffHunk(comment.diffHunk) : null
  const anchor =
    hunk !== null && finding.line !== null
      ? hunk.lines.findIndex((line) => line.newLine === finding.line)
      : -1
  const above =
    hunk === null ? [] : anchor >= 0 ? hunk.lines.slice(0, anchor + 1) : hunk.lines
  const below = hunk !== null && anchor >= 0 ? hunk.lines.slice(anchor + 1) : []

  return (
    <li className="overflow-hidden rounded-lg border border-border bg-card">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 border-b border-border bg-muted/40 px-3 py-1.5">
        <span className="font-mono text-xs text-foreground/90">{location}</span>
        <span className="text-2xs uppercase tracking-wider text-muted-foreground">
          {finding.category}
        </span>
        <span
          className={cn(
            "ml-auto inline-flex items-center rounded border px-1.5 py-px text-2xs font-semibold uppercase tracking-wider",
            SEVERITY_STYLES[finding.severity],
          )}
        >
          {SEVERITY_LABELS[finding.severity]}
        </span>
      </div>

      {comment ? (
        <>
          {hunk ? (
            <DiffHunkRows
              header={hunk.header}
              lines={above}
              commentedLine={finding.line}
              tone="top"
            />
          ) : null}
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1.5 border-b border-border px-3 py-2">
            <Avatar className="size-5">
              {/* No avatar URL is known: a GitHub App bot's avatar is not
                  addressable by its login, so the fallback is the whole
                  mark. */}
              <AvatarFallback className="text-[9px]">
                {initials(comment.name)}
              </AvatarFallback>
            </Avatar>
            <span
              className="text-[13px] font-medium text-foreground"
              title={comment.login}
            >
              {comment.name}
            </span>
            <span className="rounded-full border border-border px-1.5 text-[10px] uppercase tracking-wider text-muted-foreground">
              bot
            </span>
            <span className="text-2xs text-muted-foreground">
              commented {formatRelativeTime(comment.postedAt)}
            </span>
            <a
              href={comment.url}
              target="_blank"
              rel="noreferrer"
              className="ml-auto flex items-center gap-1 text-2xs font-medium text-accent hover:underline"
            >
              View comment
              <ExternalLink className="size-3" />
            </a>
          </div>
        </>
      ) : (
        <div className="border-b border-border px-3 py-2">
          <span
            className="text-2xs text-muted-foreground"
            title={NOT_POSTED_HINT}
          >
            Not posted inline
          </span>
        </div>
      )}

      <div className="px-3 py-2.5">
        <MessageBody message={finding.message} />
      </div>

      {finding.suggestion ? <SuggestionBlock code={finding.suggestion} /> : null}

      {below.length > 0 ? (
        <DiffHunkRows
          lines={below}
          commentedLine={finding.line}
          tone="tail"
        />
      ) : null}
    </li>
  )
}

export interface SessionTargetRowProps {
  target: SessionTarget
}

/**
 * One pull request inside a session. The findings the detail read attached
 * open under the row; a target whose findings the list left out keeps the
 * count it always showed.
 */
export function SessionTargetRow({ target }: SessionTargetRowProps) {
  // The findings are what a reader opens a target for, so the disclosure
  // starts expanded; it stays collapsible for a reader who wants it out of the
  // way.
  const [open, setOpen] = useState(true)
  const findings = target.findings ?? []

  return (
    <Collapsible open={open} onOpenChange={setOpen} className="flex flex-col">
      <div className="flex flex-col gap-2 px-4 py-3 sm:flex-row sm:items-center sm:gap-4">
        <span className="flex min-w-0 flex-1 flex-col gap-0.5">
          <a
            href={target.url}
            target="_blank"
            rel="noreferrer"
            className="flex items-center gap-1.5 font-mono text-xs text-foreground hover:text-accent"
          >
            {target.repository.fullName}#{target.number}
            {target.repository.private ? (
              <span className="rounded border border-border px-1 text-[9px] uppercase tracking-wider text-muted-foreground">
                private
              </span>
            ) : null}
            <ExternalLink className="size-3 text-muted-foreground/60" />
          </a>
          <span className="truncate text-[13px] text-muted-foreground">
            {target.title}
          </span>
        </span>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2 sm:justify-end">
          {findings.length > 0 ? (
            // A button, not a link: the row's other click targets — the PR
            // link above — keep their own jobs.
            <CollapsibleTrigger asChild>
              <button
                type="button"
                className="flex items-center gap-1 text-2xs text-muted-foreground transition-colors hover:text-foreground"
              >
                {findings.length} findings
                <ChevronDown
                  className={cn(
                    "size-3 transition-transform",
                    open && "rotate-180",
                  )}
                />
              </button>
            </CollapsibleTrigger>
          ) : (
            <span className="text-2xs text-muted-foreground">
              {target.findingsCount} findings
            </span>
          )}
          <span className="tabular font-mono text-xs text-foreground/90">
            {formatTokens(target.tokens)}
          </span>
          <span className="tabular font-mono text-xs text-foreground/90">
            {formatCost(target.costUsd)}
          </span>
          <span className="tabular font-mono text-xs text-muted-foreground/80">
            {formatDuration(target.durationMs)}
          </span>
          <SessionStatusBadge status={target.status} />
        </div>
      </div>
      {findings.length > 0 ? (
        <CollapsibleContent>
          <ul className="flex flex-col gap-3 border-t border-border bg-muted/20 px-4 py-3">
            {findings.map((finding, index) => (
              // Two findings can cite the same line, so the position in the
              // list is part of the key.
              <FindingRow
                key={`${finding.path}:${finding.line ?? 0}:${index}`}
                finding={finding}
              />
            ))}
          </ul>
        </CollapsibleContent>
      ) : null}
    </Collapsible>
  )
}
