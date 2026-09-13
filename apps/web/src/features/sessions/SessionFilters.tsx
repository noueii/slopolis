import { Search, X } from "lucide-react"

import { cn } from "@/lib/utils"
import type {
  DateRangePreset,
  SessionFilterOptions,
  SessionListParams,
  SessionStatus,
} from "@/api/contract"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { SESSION_STATUSES, STATUS_LABELS } from "./lib/status"

export interface SessionFiltersProps {
  filters: SessionFilterOptions | null
  searchInput: string
  onSearchChange: (value: string) => void
  params: SessionListParams
  onFilterChange: (patch: Partial<SessionListParams>) => void
  onClear: () => void
  activeCount: number
}

const DATE_RANGES: Array<{ value: DateRangePreset; label: string }> = [
  { value: "all", label: "All time" },
  { value: "24h", label: "Last 24 hours" },
  { value: "7d", label: "Last 7 days" },
  { value: "30d", label: "Last 30 days" },
  { value: "90d", label: "Last 90 days" },
]

const ALL = "all"

export function SessionFilters({
  filters,
  searchInput,
  onSearchChange,
  params,
  onFilterChange,
  onClear,
  activeCount,
}: SessionFiltersProps) {
  return (
    <div className="flex flex-wrap items-center gap-2 rounded-lg border border-border bg-card p-2.5">
      <div className="relative min-w-[220px] flex-1">
        <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
        <Input
          value={searchInput}
          onChange={(event) => onSearchChange(event.target.value)}
          placeholder="Search sessions, repos, PRs, users…"
          aria-label="Search sessions"
          className="h-8 border-transparent bg-muted/60 pl-8 text-[13px] shadow-none focus-visible:bg-background"
        />
      </div>

      <Select
        value={params.repo ?? ALL}
        onValueChange={(value) =>
          onFilterChange({ repo: value === ALL ? undefined : value })
        }
      >
        <SelectTrigger className="h-8 w-[196px] text-[13px]" aria-label="Filter by repository">
          <SelectValue placeholder="Repository" />
        </SelectTrigger>
        <SelectContent>
          <SelectGroup>
            <SelectItem value={ALL}>All repositories</SelectItem>
            {(filters?.repositories ?? []).map((option) => (
              <SelectItem key={option.value} value={option.value}>
                {option.label}
              </SelectItem>
            ))}
          </SelectGroup>
        </SelectContent>
      </Select>

      <Select
        value={params.user ?? ALL}
        onValueChange={(value) =>
          onFilterChange({ user: value === ALL ? undefined : value })
        }
      >
        <SelectTrigger className="h-8 w-[150px] text-[13px]" aria-label="Filter by user">
          <SelectValue placeholder="User" />
        </SelectTrigger>
        <SelectContent>
          <SelectGroup>
            <SelectItem value={ALL}>All users</SelectItem>
            {(filters?.users ?? []).map((option) => (
              <SelectItem key={option.value} value={option.value}>
                {option.label}
              </SelectItem>
            ))}
          </SelectGroup>
        </SelectContent>
      </Select>

      <Select
        value={params.status ?? ALL}
        onValueChange={(value) =>
          onFilterChange({
            status: value === ALL ? undefined : (value as SessionStatus),
          })
        }
      >
        <SelectTrigger className="h-8 w-[140px] text-[13px]" aria-label="Filter by status">
          <SelectValue placeholder="Status" />
        </SelectTrigger>
        <SelectContent>
          <SelectGroup>
            <SelectItem value={ALL}>All statuses</SelectItem>
            {SESSION_STATUSES.map((status) => (
              <SelectItem key={status} value={status}>
                {STATUS_LABELS[status]}
              </SelectItem>
            ))}
          </SelectGroup>
        </SelectContent>
      </Select>

      <Select
        value={params.range ?? "all"}
        onValueChange={(value) =>
          onFilterChange({ range: value as DateRangePreset })
        }
      >
        <SelectTrigger className="h-8 w-[148px] text-[13px]" aria-label="Filter by date">
          <SelectValue placeholder="Date" />
        </SelectTrigger>
        <SelectContent>
          <SelectGroup>
            {DATE_RANGES.map((option) => (
              <SelectItem key={option.value} value={option.value}>
                {option.label}
              </SelectItem>
            ))}
          </SelectGroup>
        </SelectContent>
      </Select>

      {activeCount > 0 ? (
        <Button
          variant="ghost"
          size="sm"
          className={cn("h-8 gap-1.5 px-2 text-[13px] text-muted-foreground")}
          onClick={onClear}
        >
          <X className="size-3.5" />
          Clear
          <span className="grid size-4 place-items-center rounded-full bg-accent text-[10px] font-semibold text-accent-foreground">
            {activeCount}
          </span>
        </Button>
      ) : null}
    </div>
  )
}
