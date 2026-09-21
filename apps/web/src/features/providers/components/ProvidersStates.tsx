import { AlertTriangle, RotateCw } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"

export function ProvidersSkeleton() {
  return (
    <div className="flex flex-col gap-6" aria-hidden>
      {Array.from({ length: 3 }).map((_, section) => (
        <div
          key={section}
          className="overflow-hidden rounded-lg border border-border bg-card"
        >
          <div className="flex items-center justify-between gap-3 border-b border-border px-4 py-3">
            <Skeleton className="h-4 w-40" />
            <Skeleton className="h-8 w-32" />
          </div>
          {Array.from({ length: 2 }).map((_, row) => (
            <div
              key={row}
              className="flex items-center gap-3 border-b border-border px-4 py-3 last:border-b-0"
            >
              <Skeleton className="h-3.5 flex-1" />
              <Skeleton className="h-3.5 w-24" />
              <Skeleton className="h-7 w-28" />
            </div>
          ))}
        </div>
      ))}
    </div>
  )
}

export interface ProvidersErrorProps {
  message: string
  onRetry: () => void
}

export function ProvidersError({ message, onRetry }: ProvidersErrorProps) {
  return (
    <div className="flex flex-col items-start gap-3 rounded-lg border border-destructive/30 bg-destructive/5 px-6 py-8">
      <div className="flex items-center gap-2.5">
        <AlertTriangle className="size-4 text-destructive" />
        <h2 className="text-sm font-semibold tracking-tight text-destructive">
          Could not load provider settings
        </h2>
      </div>
      <p className="max-w-2xl text-sm leading-relaxed text-muted-foreground">
        {message}
      </p>
      <Button variant="outline" size="sm" onClick={onRetry}>
        <RotateCw data-icon="inline-start" />
        Try again
      </Button>
    </div>
  )
}
