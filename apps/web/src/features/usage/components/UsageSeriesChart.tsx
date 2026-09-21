import type { UsagePoint } from "@/api/contract"
import { formatCost, formatTokens } from "@/features/sessions/lib/format"

export interface UsageSeriesChartProps {
  series: UsagePoint[]
}

const DAY_LABEL = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
  // Buckets are calendar days; formatting a bare date in local time can shift
  // the day for anyone west of UTC, so anchor the parse and the format to UTC.
  timeZone: "UTC",
})

function bucketDate(iso: string): Date {
  return new Date(`${iso}T00:00:00Z`)
}

export function UsageSeriesChart({ series }: UsageSeriesChartProps) {
  if (series.length === 0) {
    return (
      <section
        aria-label="Daily usage"
        className="flex flex-col gap-3 rounded-lg border border-border bg-card px-4 py-4"
      >
        <h2 className="text-sm font-semibold tracking-tight">Daily usage</h2>
        <p className="py-8 text-center text-sm text-muted-foreground">
          No daily buckets recorded yet.
        </p>
      </section>
    )
  }

  const first = series[0]
  const last = series[series.length - 1]
  const peak = series.reduce((best, point) =>
    point.tokens > best.tokens ? point : best,
  )
  const totalTokens = series.reduce((sum, point) => sum + point.tokens, 0)
  const maxTokens = Math.max(peak.tokens, 1)

  // The bars carry no text of their own, so the label spells the series out for
  // screen readers and the table below keeps every number readable without colour.
  const summary = `Daily usage from ${DAY_LABEL.format(bucketDate(first.date))} to ${DAY_LABEL.format(bucketDate(last.date))}: ${series.length} days, ${formatTokens(totalTokens)} tokens in total, peaking at ${formatTokens(peak.tokens)} on ${DAY_LABEL.format(bucketDate(peak.date))}.`

  return (
    <section
      aria-label="Daily usage"
      className="flex flex-col gap-3 rounded-lg border border-border bg-card px-4 py-4"
    >
      <header className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-sm font-semibold tracking-tight">Daily usage</h2>
        <p className="font-mono text-2xs text-muted-foreground">
          {series.length} days · peak {formatTokens(peak.tokens)} on{" "}
          {DAY_LABEL.format(bucketDate(peak.date))} ·{" "}
          {formatTokens(totalTokens)} tokens
        </p>
      </header>

      <div
        role="img"
        aria-label={summary}
        className="flex h-[132px] items-end gap-1"
      >
        {series.map((point) => (
          <div
            key={point.date}
            className="relative h-full flex-1 overflow-hidden rounded-sm bg-muted/40"
          >
            <div
              className="absolute inset-x-0 bottom-0 rounded-sm bg-accent"
              style={{ height: `${(point.tokens / maxTokens) * 100}%` }}
            />
          </div>
        ))}
      </div>

      {series.length > 1 ? (
        <div className="flex justify-between font-mono text-2xs text-muted-foreground">
          <span>{DAY_LABEL.format(bucketDate(first.date))}</span>
          <span>{DAY_LABEL.format(bucketDate(last.date))}</span>
        </div>
      ) : null}

      <table className="sr-only">
        <caption>Daily usage</caption>
        <thead>
          <tr>
            <th scope="col">Day</th>
            <th scope="col">Tokens</th>
            <th scope="col">Cost</th>
            <th scope="col">Sessions</th>
          </tr>
        </thead>
        <tbody>
          {series.map((point) => (
            <tr key={point.date}>
              <th scope="row">{DAY_LABEL.format(bucketDate(point.date))}</th>
              <td>{formatTokens(point.tokens)}</td>
              <td>{formatCost(point.costUsd)}</td>
              <td>{point.sessions}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  )
}
