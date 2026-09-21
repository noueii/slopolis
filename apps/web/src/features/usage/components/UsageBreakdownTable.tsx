import type { UsageBreakdown } from "@/api/contract"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { formatCost, formatCount, formatTokens } from "@/features/sessions/lib/format"

export interface UsageBreakdownTableProps {
  /** Section heading, e.g. "By model". */
  title: string
  /** Column heading for the dimension value, e.g. "Model". */
  dimension: string
  emptyMessage: string
  rows: UsageBreakdown[]
  total: { tokens: number; costUsd: number }
  /**
   * Whether a row may print its machine key under the label when the two
   * differ — that is what makes a model row traceable to its model id.
   * Defaults to true; a dimension keyed by an internal id turns it off, since
   * the key is then nothing a reader could act on.
   */
  showKey?: boolean
}

export function UsageBreakdownTable({
  title,
  dimension,
  emptyMessage,
  rows,
  total,
  showKey = true,
}: UsageBreakdownTableProps) {
  // Spend is the headline of this page, so the share bar reads against cost —
  // unless nothing has a price yet, in which case tokens are all there is.
  const byCost = total.costUsd > 0
  const denominator = byCost ? total.costUsd : total.tokens
  const shareLabel = byCost ? "Share of cost" : "Share of tokens"

  // The API orders every breakdown by tokens, which is the metric the bars
  // measure only when it falls back to tokens. Reading down a cost share column
  // that jumps 60% → 11% → 2% → 16% looks arbitrary, so the rows follow the
  // metric on screen (ties by tokens, then key, so the order is total).
  const ordered = byCost
    ? [...rows].sort(
        (a, b) =>
          b.costUsd - a.costUsd ||
          b.tokens - a.tokens ||
          a.key.localeCompare(b.key),
      )
    : rows

  return (
    <section
      aria-label={title}
      className="overflow-hidden rounded-lg border border-border bg-card"
    >
      <header className="flex items-baseline justify-between gap-3 border-b border-border px-4 py-3">
        <h2 className="text-sm font-semibold tracking-tight">{title}</h2>
        {ordered.length > 0 ? (
          <span className="font-mono text-2xs text-muted-foreground">
            {formatCount(ordered.length)}{" "}
            {ordered.length === 1 ? "entry" : "entries"}
          </span>
        ) : null}
      </header>

      <Table aria-label={title}>
        <TableHeader>
          <TableRow className="hover:bg-transparent">
            <TableHead className="h-9 pl-4 text-2xs uppercase tracking-wider">
              {dimension}
            </TableHead>
            <TableHead className="h-9 w-[140px] text-2xs uppercase tracking-wider">
              {shareLabel}
            </TableHead>
            <TableHead className="h-9 text-right text-2xs uppercase tracking-wider">
              Tokens
            </TableHead>
            <TableHead className="h-9 text-right text-2xs uppercase tracking-wider">
              Cost
            </TableHead>
            <TableHead className="h-9 pr-4 text-right text-2xs uppercase tracking-wider">
              Sessions
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {ordered.length === 0 ? (
            <TableRow className="hover:bg-transparent">
              <TableCell
                colSpan={5}
                className="px-4 py-8 text-center text-sm text-muted-foreground"
              >
                {emptyMessage}
              </TableCell>
            </TableRow>
          ) : null}

          {ordered.map((row) => {
            const part = byCost ? row.costUsd : row.tokens
            const percent = denominator > 0 ? (part / denominator) * 100 : 0

            return (
              <TableRow key={row.key}>
                <TableHead
                  scope="row"
                  className="py-2.5 pl-4 font-medium text-foreground"
                >
                  <span className="block max-w-[320px] truncate text-[13px]">
                    {row.label}
                  </span>
                  {showKey && row.key !== row.label ? (
                    <span className="block max-w-[320px] truncate font-mono text-[10px] font-normal text-muted-foreground">
                      {row.key}
                    </span>
                  ) : null}
                </TableHead>
                <TableCell className="py-2.5">
                  <div className="flex items-center gap-2">
                    {/* The bar is the glanceable cue; the percentage beside it
                        carries the same fact as text. */}
                    <div
                      aria-hidden
                      className="h-1.5 w-16 shrink-0 overflow-hidden rounded-full bg-muted"
                    >
                      <div
                        className="h-full rounded-full bg-accent"
                        style={{ width: `${Math.min(100, Math.max(0, percent))}%` }}
                      />
                    </div>
                    <span className="tabular font-mono text-2xs text-muted-foreground">
                      {percent > 0 && percent < 1 ? "<1%" : `${Math.round(percent)}%`}
                    </span>
                  </div>
                </TableCell>
                <TableCell className="tabular py-2.5 text-right font-mono text-xs text-foreground/90">
                  {formatTokens(row.tokens)}
                </TableCell>
                <TableCell className="tabular py-2.5 text-right font-mono text-xs text-foreground/90">
                  {formatCost(row.costUsd)}
                </TableCell>
                <TableCell className="tabular py-2.5 pr-4 text-right font-mono text-xs text-foreground/90">
                  {formatCount(row.sessions)}
                </TableCell>
              </TableRow>
            )
          })}
        </TableBody>
      </Table>
    </section>
  )
}
