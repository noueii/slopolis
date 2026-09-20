import { ChevronRight, RotateCw } from "lucide-react"
import { useState } from "react"

import type { AgentRunNode, SessionStatus } from "@/api/contract"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { cn } from "@/lib/utils"

import { formatCost, formatTokens } from "../lib/format"
import { countRuns, isPreviousAttempt } from "../lib/runTree"
import type { RunTreeStatus } from "../lib/useRunTree"
import { RunLevelChip, RunStatusBadge } from "./RunStatusBadge"

export interface RunTreePaneProps {
  runs: AgentRunNode[]
  /** Session status: a run that ended while the session is in flight is history. */
  sessionStatus: SessionStatus
  status: RunTreeStatus
  error: string | null
  selectedRunId: string | null
  onSelect: (runId: string) => void
  onRetry: () => void
}

/**
 * The session → PRs → sub-agents tree (spec v2 §7 §UI). Each row is selectable:
 * the node detail pane renders whichever run is selected.
 */
export function RunTreePane({
  runs,
  sessionStatus,
  status,
  error,
  selectedRunId,
  onSelect,
  onRetry,
}: RunTreePaneProps) {
  const total = countRuns(runs)
  const failed = countFailed(runs)

  return (
    <section
      aria-label="Agent run tree"
      className="flex flex-col gap-3 rounded-lg border border-border bg-card p-3"
    >
      <header className="flex items-center justify-between gap-3 px-1">
        <h2 className="text-2xs font-semibold uppercase tracking-widest text-muted-foreground">
          Run tree
        </h2>
        {total > 0 ? (
          <span className="tabular font-mono text-2xs text-muted-foreground">
            {total} {total === 1 ? "run" : "runs"}
            {failed > 0 ? ` · ${failed} failed` : ""}
          </span>
        ) : null}
      </header>

      {status === "error" ? (
        <div className="flex flex-col items-start gap-3 rounded-lg border border-destructive/30 bg-destructive/5 px-4 py-5">
          <p className="text-[13px] text-muted-foreground">
            {error ?? "Could not load the agent run tree."}
          </p>
          <Button variant="outline" size="sm" onClick={onRetry}>
            <RotateCw data-icon="inline-start" />
            Try again
          </Button>
        </div>
      ) : null}

      {status === "loading" && total === 0 ? (
        <div className="flex flex-col gap-2">
          {Array.from({ length: 4 }).map((_, index) => (
            <Skeleton key={index} className="h-10 rounded-lg" />
          ))}
        </div>
      ) : null}

      {status === "success" && total === 0 ? (
        <p className="rounded-lg border border-dashed border-border bg-card/50 px-4 py-6 text-[13px] text-muted-foreground">
          No agent runs yet. The tree appears here once this session starts
          delegating.
        </p>
      ) : null}

      {total > 0 ? (
        <ul className="flex flex-col gap-0.5">
          {runs.map((node) => (
            <RunTreeRow
              key={node.id}
              node={node}
              sessionStatus={sessionStatus}
              selectedRunId={selectedRunId}
              onSelect={onSelect}
            />
          ))}
        </ul>
      ) : null}
    </section>
  )
}

function countFailed(runs: AgentRunNode[]): number {
  let total = 0
  for (const node of runs) {
    if (node.status === "failed") total += 1
    total += countFailed(node.children)
  }
  return total
}

function RunTreeRow({
  node,
  sessionStatus,
  selectedRunId,
  onSelect,
}: {
  node: AgentRunNode
  sessionStatus: SessionStatus
  selectedRunId: string | null
  onSelect: (runId: string) => void
}) {
  const [open, setOpen] = useState(true)
  const selected = node.id === selectedRunId
  const hasChildren = node.children.length > 0
  const previousAttempt = isPreviousAttempt(sessionStatus, node.status)

  return (
    <li>
      <div
        className={cn(
          "flex items-center gap-1 rounded-lg border px-1 py-1 transition-colors",
          selected
            ? "border-accent/40 bg-accent/5"
            : "border-transparent hover:bg-muted/40",
        )}
      >
        {hasChildren ? (
          <button
            type="button"
            aria-expanded={open}
            aria-label={`${open ? "Collapse" : "Expand"} ${node.role}`}
            onClick={() => setOpen((value) => !value)}
            className="flex size-5 shrink-0 items-center justify-center rounded text-muted-foreground hover:text-foreground"
          >
            <ChevronRight
              className={cn(
                "size-3.5 transition-transform",
                open ? "rotate-90" : undefined,
              )}
            />
          </button>
        ) : (
          <span aria-hidden className="size-5 shrink-0" />
        )}

        <button
          type="button"
          aria-current={selected}
          aria-label={`${node.role} — ${node.objective}`}
          onClick={() => {
            onSelect(node.id)
            if (hasChildren) setOpen(true)
          }}
          className="flex min-w-0 flex-1 items-center gap-2 rounded px-1 py-1 text-left"
        >
          <RunLevelChip level={node.level} />
          <span className="flex min-w-0 flex-1 flex-col gap-0.5">
            <span
              className={cn(
                "truncate text-[13px]",
                node.objective ? "text-foreground" : "text-muted-foreground",
              )}
            >
              {node.objective || `${node.role} (no objective recorded)`}
            </span>
            <span className="truncate font-mono text-2xs text-muted-foreground">
              {node.role}
              {node.modelId ? ` · ${node.modelId}` : " · model pending"}
            </span>
          </span>
          <span className="hidden shrink-0 items-center gap-2 sm:flex">
            <span className="tabular font-mono text-2xs text-muted-foreground">
              {formatTokens(node.tokens)}
            </span>
            <span className="tabular font-mono text-2xs text-muted-foreground/80">
              {formatCost(node.costUsd)}
            </span>
          </span>
          <RunStatusBadge status={node.status} className="shrink-0" />
        </button>

        {previousAttempt ? (
          <Badge
            variant="outline"
            className="shrink-0 px-1.5 py-0 text-2xs font-semibold uppercase tracking-wider text-muted-foreground"
          >
            previous attempt
          </Badge>
        ) : null}
      </div>

      {hasChildren && open ? (
        <ul className="ml-3 flex flex-col gap-0.5 border-l border-border/70 pl-2">
          {node.children.map((child) => (
            <RunTreeRow
              key={child.id}
              node={child}
              sessionStatus={sessionStatus}
              selectedRunId={selectedRunId}
              onSelect={onSelect}
            />
          ))}
        </ul>
      ) : null}
    </li>
  )
}
