import { cn } from "@/lib/utils"
import { formatCost, formatCount, formatTokens } from "@/features/sessions/lib/format"

export interface UsageTotalsProps {
  tokens: number
  costUsd: number
  sessions: number
}

export function UsageTotals({ tokens, costUsd, sessions }: UsageTotalsProps) {
  const cells = [
    {
      label: "Tokens",
      value: formatTokens(tokens),
      hint: `${formatCount(tokens)} attributed`,
      lead: true,
    },
    {
      label: "Tracked spend",
      value: formatCost(costUsd),
      hint: "reported and estimated",
    },
    {
      label: "Sessions",
      value: formatCount(sessions),
      hint: "with recorded usage",
    },
  ]

  return (
    <section aria-label="Usage totals">
      <dl className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        {cells.map((cell) => (
          <div
            key={cell.label}
            className={cn(
              "flex flex-col gap-1 rounded-lg border border-border bg-card px-4 py-3",
              cell.lead && "border-accent/30 bg-accent/[0.04]",
            )}
          >
            <dt className="text-2xs font-semibold uppercase tracking-widest text-muted-foreground">
              {cell.label}
            </dt>
            <dd className="tabular font-mono text-lg font-semibold tracking-tight text-foreground">
              {cell.value}
            </dd>
            <span className="text-2xs text-muted-foreground/75">{cell.hint}</span>
          </div>
        ))}
      </dl>
    </section>
  )
}
