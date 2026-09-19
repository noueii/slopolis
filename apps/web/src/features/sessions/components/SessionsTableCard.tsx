import type { ReactNode } from "react"

import { cn } from "@/lib/utils"

export interface SessionsTableCardProps {
  /** The table to render inside the horizontally scrollable region. */
  children: ReactNode
  /** Optional content rendered below the scroll region, outside of it (e.g. pagination). */
  footer?: ReactNode
  /** Dims and blocks interaction while a refetch is in flight. */
  busy?: boolean
}

/**
 * Shared card shell for session tables.
 *
 * Wide column sets (Tokens, Cost, Triggered by, Created, …) must stay reachable
 * instead of being clipped by the card's rounded border, so the table lives in a
 * dedicated horizontally scrollable region (`overflow-x-auto` + `scrollbar-thin`)
 * whose content is sized to the table's intrinsic width (`min-w-max`).
 */
export function SessionsTableCard({
  children,
  footer,
  busy = false,
}: SessionsTableCardProps) {
  return (
    <div
      className={cn(
        "overflow-hidden rounded-lg border border-border bg-card transition-opacity",
        busy && "pointer-events-none opacity-60",
      )}
    >
      <div className="w-full overflow-x-auto scrollbar-thin">
        <div className="min-w-max">{children}</div>
      </div>
      {footer}
    </div>
  )
}
