import { AlertTriangle, RotateCw } from "lucide-react"

import { Button } from "@/components/ui/button"

export interface SessionsErrorProps {
  message: string
  onRetry: () => void
}

export function SessionsError({ message, onRetry }: SessionsErrorProps) {
  return (
    <div className="flex flex-col items-start gap-3 rounded-lg border border-destructive/30 bg-destructive/5 px-6 py-8">
      <div className="flex items-center gap-2.5">
        <AlertTriangle className="size-4 text-destructive" />
        <h2 className="text-sm font-semibold tracking-tight text-destructive">
          Could not load sessions
        </h2>
      </div>
      <p className="max-w-xl text-sm text-muted-foreground">{message}</p>
      <Button variant="outline" size="sm" onClick={onRetry}>
        <RotateCw data-icon="inline-start" />
        Try again
      </Button>
    </div>
  )
}
