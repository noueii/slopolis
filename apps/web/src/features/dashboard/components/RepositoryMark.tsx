import { Globe, Lock } from "lucide-react"

import { cn } from "@/lib/utils"
import { repoMonogram } from "../lib/selection"

type MarkSize = "sm" | "md" | "lg"

const SIZES: Record<MarkSize, string> = {
  sm: "size-6 rounded text-[10px]",
  md: "size-8 rounded-md text-[11px]",
  lg: "size-10 rounded-lg text-[13px]",
}

const ICON_SIZES: Record<MarkSize, string> = {
  sm: "size-2.5",
  md: "size-3",
  lg: "size-3.5",
}

export interface RepositoryMarkProps {
  fullName: string
  private?: boolean
  size?: MarkSize
  className?: string
}

export function RepositoryMark({
  fullName,
  private: isPrivate = false,
  size = "md",
  className,
}: RepositoryMarkProps) {
  return (
    <span className="relative inline-flex shrink-0">
      <span
        className={cn(
          "grid place-items-center border border-border bg-secondary font-mono font-semibold uppercase tracking-wide text-foreground/75",
          SIZES[size],
          className,
        )}
        aria-hidden
      >
        {repoMonogram(fullName)}
      </span>
      <span
        className={cn(
          "absolute -bottom-1 -right-1 grid place-items-center rounded-full border border-border bg-card text-muted-foreground",
          size === "sm" ? "size-3" : "size-3.5",
        )}
        aria-hidden
      >
        {isPrivate ? (
          <Lock className={ICON_SIZES[size]} />
        ) : (
          <Globe className={ICON_SIZES[size]} />
        )}
      </span>
    </span>
  )
}
