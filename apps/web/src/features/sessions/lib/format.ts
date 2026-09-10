/** Presentation helpers for session data. Pure, dependency-free. */

export function formatTokens(tokens: number): string {
  if (tokens <= 0) return "—"
  if (tokens < 1_000) return `${tokens}`
  if (tokens < 1_000_000) {
    const value = tokens / 1_000
    return `${value >= 100 ? Math.round(value) : value.toFixed(1)}k`
  }
  return `${(tokens / 1_000_000).toFixed(2)}M`
}

export function formatCost(usd: number): string {
  if (usd <= 0) return "$0.00"
  if (usd < 0.01) return `<$0.01`
  if (usd < 1000) return `$${usd.toFixed(2)}`
  return `$${(usd / 1000).toFixed(1)}k`
}

export function formatCount(value: number): string {
  return new Intl.NumberFormat("en-US").format(value)
}

export function formatDuration(ms?: number): string {
  if (ms === undefined || ms <= 0) return "—"
  const seconds = Math.round(ms / 1000)
  if (seconds < 60) return `${seconds}s`
  const minutes = Math.floor(seconds / 60)
  const remSeconds = seconds % 60
  if (minutes < 60) return `${minutes}m ${remSeconds}s`
  const hours = Math.floor(minutes / 60)
  return `${hours}h ${minutes % 60}m`
}

const MONTHS = [
  "Jan",
  "Feb",
  "Mar",
  "Apr",
  "May",
  "Jun",
  "Jul",
  "Aug",
  "Sep",
  "Oct",
  "Nov",
  "Dec",
]

export function formatAbsoluteTime(iso: string): string {
  const date = new Date(iso)
  const time = `${date.getHours().toString().padStart(2, "0")}:${date
    .getMinutes()
    .toString()
    .padStart(2, "0")}`
  return `${MONTHS[date.getMonth()]} ${date.getDate()}, ${time}`
}

export function formatRelativeTime(iso: string, now = Date.now()): string {
  const then = new Date(iso).getTime()
  const delta = now - then
  const minute = 60_000
  const hour = 60 * minute
  const day = 24 * hour

  if (delta < minute) return "just now"
  if (delta < hour) return `${Math.floor(delta / minute)}m ago`
  if (delta < day) return `${Math.floor(delta / hour)}h ago`
  if (delta < 7 * day) return `${Math.floor(delta / day)}d ago`
  return formatAbsoluteTime(iso)
}

export function initials(name: string): string {
  const parts = name.trim().split(/\s+/)
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase()
  return `${parts[0][0]}${parts[parts.length - 1][0]}`.toUpperCase()
}
