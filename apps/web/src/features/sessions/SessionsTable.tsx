import {
  ArrowDown,
  ArrowUp,
  ArrowUpDown,
  ChevronRight,
} from "lucide-react"

import { cn } from "@/lib/utils"
import type { ReviewSession, SessionSort } from "@/api/contract"
import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { SessionStatusBadge } from "./components/SessionStatusBadge"
import {
  formatAbsoluteTime,
  formatCost,
  formatRelativeTime,
  formatTokens,
  initials,
} from "./lib/format"

export interface SessionsTableProps {
  sessions: ReviewSession[]
  sort: SessionSort
  onSortChange: (sort: SessionSort) => void
  onOpenSession: (id: string) => void
}

interface SortHeaderProps {
  label: string
  active: boolean
  direction: "asc" | "desc"
  onClick: () => void
  className?: string
}

function SortHeader({
  label,
  active,
  direction,
  onClick,
  className,
}: SortHeaderProps) {
  const Icon = !active ? ArrowUpDown : direction === "asc" ? ArrowUp : ArrowDown
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "inline-flex items-center gap-1 rounded transition-colors hover:text-foreground",
        active && "text-foreground",
        className,
      )}
    >
      {label}
      <Icon className={cn("size-3", !active && "opacity-40")} />
    </button>
  )
}

function TargetsCell({ session }: { session: ReviewSession }) {
  const targets = session.targets
  const visible = targets.slice(0, 2)
  const overflow = targets.length - visible.length
  const title = targets
    .map((target) => `${target.repository.fullName}#${target.number}`)
    .join(", ")

  return (
    <div className="flex flex-wrap items-center gap-1" title={title}>
      {visible.map((target) => (
        <span
          key={target.id}
          className="whitespace-nowrap rounded border border-border bg-muted/50 px-1.5 py-0.5 font-mono text-[11px] text-foreground/80"
        >
          {target.repository.fullName.split("/")[1]}#{target.number}
        </span>
      ))}
      {overflow > 0 ? (
        <span className="font-mono text-[11px] text-muted-foreground">
          +{overflow} more
        </span>
      ) : null}
    </div>
  )
}

export function SessionsTable({
  sessions,
  sort,
  onSortChange,
  onOpenSession,
}: SessionsTableProps) {
  const createdDesc = sort !== "created_asc"

  return (
    <Table>
      <TableHeader>
          <TableRow className="hover:bg-transparent">
            <TableHead className="h-10 w-[30%] pl-4 text-2xs uppercase tracking-wider">
              Session
            </TableHead>
            <TableHead className="text-2xs uppercase tracking-wider">
              Targets
            </TableHead>
            <TableHead className="w-[112px] text-2xs uppercase tracking-wider">
              Status
            </TableHead>
            <TableHead className="w-[150px] text-2xs uppercase tracking-wider">
              Model
            </TableHead>
            <TableHead className="text-right text-2xs uppercase tracking-wider">
              <SortHeader
                label="Tokens"
                active={sort === "tokens_desc"}
                direction="desc"
                onClick={() => onSortChange("tokens_desc")}
              />
            </TableHead>
            <TableHead className="text-right text-2xs uppercase tracking-wider">
              <SortHeader
                label="Cost"
                active={sort === "cost_desc"}
                direction="desc"
                onClick={() => onSortChange("cost_desc")}
              />
            </TableHead>
            <TableHead className="whitespace-nowrap text-2xs uppercase tracking-wider">
              Triggered by
            </TableHead>
            <TableHead className="text-2xs uppercase tracking-wider">
              <SortHeader
                label="Created"
                active
                direction={createdDesc ? "desc" : "asc"}
                onClick={() =>
                  onSortChange(createdDesc ? "created_asc" : "created_desc")
                }
              />
            </TableHead>
            <TableHead className="w-8 pr-3" />
          </TableRow>
        </TableHeader>
        <TableBody>
          {sessions.map((session) => (
            <TableRow
              key={session.id}
              tabIndex={0}
              aria-label={`Open session ${session.name}`}
              onClick={() => onOpenSession(session.id)}
              onKeyDown={(event) => {
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault()
                  onOpenSession(session.id)
                }
              }}
              className="cursor-pointer focus-visible:bg-muted/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
            >
              <TableCell className="py-2.5 pl-4">
                <span className="block max-w-[340px] truncate text-[13px] font-medium text-foreground">
                  {session.name}
                </span>
                <span className="mt-0.5 flex items-center gap-2 font-mono text-[10px] text-muted-foreground">
                  <span>{session.id}</span>
                  {session.findingsCount > 0 ? (
                    <span className="flex items-center gap-1">
                      <span className="size-1 rounded-full bg-muted-foreground/50" />
                      {session.findingsCount} findings
                    </span>
                  ) : null}
                </span>
              </TableCell>
              <TableCell className="py-2.5">
                <TargetsCell session={session} />
              </TableCell>
              <TableCell className="py-2.5">
                <SessionStatusBadge status={session.status} />
              </TableCell>
              <TableCell className="py-2.5">
                <span className="block whitespace-nowrap font-mono text-xs text-foreground">
                  {session.model}
                </span>
                <span className="whitespace-nowrap text-2xs text-muted-foreground">
                  {session.provider}
                </span>
              </TableCell>
              <TableCell className="tabular py-2.5 text-right font-mono text-xs text-foreground/90">
                {formatTokens(session.tokens)}
              </TableCell>
              <TableCell className="tabular py-2.5 text-right font-mono text-xs text-foreground/90">
                {formatCost(session.costUsd)}
              </TableCell>
              <TableCell className="py-2.5">
                <div className="flex items-center gap-2">
                  <Avatar className="size-5 border border-border/70">
                    <AvatarFallback className="bg-muted text-[9px] font-semibold text-muted-foreground">
                      {initials(session.triggeredBy.name)}
                    </AvatarFallback>
                  </Avatar>
                  <span className="whitespace-nowrap font-mono text-[11px] text-muted-foreground">
                    @{session.triggeredBy.handle}
                  </span>
                </div>
              </TableCell>
              <TableCell className="py-2.5">
                <span
                  className="text-xs text-foreground/90"
                  title={formatAbsoluteTime(session.createdAt)}
                >
                  {formatRelativeTime(session.createdAt)}
                </span>
                <span className="mt-0.5 block font-mono text-[10px] text-muted-foreground">
                  {formatAbsoluteTime(session.createdAt)}
                </span>
              </TableCell>
              <TableCell className="pr-3">
                <ChevronRight className="size-3.5 text-muted-foreground/60" />
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
    </Table>
  )
}
