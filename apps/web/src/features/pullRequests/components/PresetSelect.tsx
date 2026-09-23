import { AlertTriangle, Loader2 } from "lucide-react"

import type { ReviewPresetCatalog } from "@/api/contract"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"

export interface PresetSelectProps {
  preset: string
  onPresetChange: (value: string) => void
  catalog: ReviewPresetCatalog | null
  status: "loading" | "success" | "error"
  onRetry?: () => void
}

const TRIGGER =
  "inline-flex h-8 w-auto max-w-[220px] items-center gap-1 rounded-lg border-0 bg-transparent px-2 text-[12px] font-medium text-muted-foreground shadow-none transition-colors hover:bg-muted/50 hover:text-foreground focus:ring-0 focus-visible:ring-1 focus-visible:ring-ring data-[state=open]:bg-muted/50"

export function PresetSelect({
  preset,
  onPresetChange,
  catalog,
  status,
  onRetry,
}: PresetSelectProps) {
  if (status === "loading") {
    return (
      <span
        className="inline-flex h-8 items-center gap-1.5 px-2 text-[12px] text-muted-foreground"
        aria-live="polite"
      >
        <Loader2 className="size-3 animate-spin" />
        <span className="hidden sm:inline">Loading presets…</span>
        <span className="sm:hidden">Presets…</span>
      </span>
    )
  }

  if (status === "error") {
    return (
      <button
        type="button"
        onClick={onRetry}
        aria-label="Presets unavailable, retry"
        className="inline-flex h-8 items-center gap-1.5 rounded-lg px-2 text-[12px] text-muted-foreground transition-colors hover:bg-muted/50 hover:text-foreground"
      >
        <AlertTriangle className="size-3 text-destructive" />
        <span className="hidden sm:inline">Presets unavailable · retry</span>
        <span className="sm:hidden">Retry</span>
      </button>
    )
  }

  const options = catalog?.presets ?? []
  const current = options.find((option) => option.id === preset)

  return (
    <Select value={preset} onValueChange={onPresetChange}>
      <SelectTrigger aria-label="Review preset" className={TRIGGER}>
        <SelectValue placeholder="Default">{current?.name ?? "Default"}</SelectValue>
      </SelectTrigger>
      <SelectContent align="start" className="min-w-[240px]">
        {options.map((option) => (
          <SelectItem
            key={option.id}
            value={option.id}
            className="items-start py-2 text-xs"
          >
            <span className="flex flex-col gap-0.5">
              <span className="font-medium">{option.name}</span>
              <span className="text-2xs leading-snug text-muted-foreground">
                {option.description}
              </span>
            </span>
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}
