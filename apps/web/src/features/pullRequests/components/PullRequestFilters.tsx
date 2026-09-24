/**
 * The inbox filter bar (spec v3 §3).
 *
 * Every facet is server-side and single-select; changing one resets the page
 * and re-requests immediately. Only the search text is debounced, which the
 * screen owns. Option labels and their counts come from the response's
 * `filterOptions`, so the bar says what the request said instead of guessing
 * from the rows on screen.
 */

import type { Ref } from "react"
import { Search, X } from "lucide-react"

import { cn } from "@/lib/utils"
import type {
  FilterOption,
  PullRequestChecks,
  PullRequestFilterOptions,
  PullRequestListParams,
  PullRequestReviewFilter,
  PullRequestSort,
} from "@/api/contract"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"

export interface PullRequestFiltersProps {
  filters: PullRequestFilterOptions | null
  params: PullRequestListParams
  searchInput: string
  onSearchChange: (value: string) => void
  onFilterChange: (patch: Partial<PullRequestListParams>) => void
  onClear: () => void
  activeCount: number
  searchRef?: Ref<HTMLInputElement>
}

const ALL = "all"

const SORTS: Array<{ value: PullRequestSort; label: string }> = [
  { value: "updated_desc", label: "Recently updated" },
  { value: "size_desc", label: "Largest diff" },
  { value: "staleness_desc", label: "Most behind" },
  { value: "created_desc", label: "Newest first" },
]

function FacetItem({ option }: { option: FilterOption }) {
  return (
    <span className="flex w-full items-center gap-4">
      <span className="flex-1 truncate">{option.label}</span>
      {option.hint ? (
        <span className="tabular ml-auto font-mono text-2xs text-muted-foreground">
          {option.hint}
        </span>
      ) : null}
    </span>
  )
}

export function PullRequestFilters({
  filters,
  params,
  searchInput,
  onSearchChange,
  onFilterChange,
  onClear,
  activeCount,
  searchRef,
}: PullRequestFiltersProps) {
  return (
    <div className="flex flex-wrap items-center gap-2 rounded-lg border border-border bg-card p-2.5">
      <div className="relative min-w-[220px] flex-1">
        <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
        <Input
          ref={searchRef}
          value={searchInput}
          onChange={(event) => onSearchChange(event.target.value)}
          placeholder="Search pull requests, repos, authors…"
          aria-label="Search pull requests"
          className="h-8 border-transparent bg-muted/60 pl-8 text-[13px] shadow-none focus-visible:bg-background"
        />
      </div>

      <Select
        value={params.repo ?? ALL}
        onValueChange={(value) =>
          onFilterChange({ repo: value === ALL ? undefined : value })
        }
      >
        <SelectTrigger
          className="h-8 w-[196px] text-[13px]"
          aria-label="Filter by repository"
        >
          <SelectValue placeholder="Repository" />
        </SelectTrigger>
        <SelectContent>
          <SelectGroup>
            <SelectItem value={ALL}>All repositories</SelectItem>
            {(filters?.repositories ?? []).map((option) => (
              <SelectItem key={option.value} value={option.value}>
                <FacetItem option={option} />
              </SelectItem>
            ))}
          </SelectGroup>
        </SelectContent>
      </Select>

      <Select
        value={params.review ?? ALL}
        onValueChange={(value) =>
          onFilterChange({
            review: value === ALL ? undefined : (value as PullRequestReviewFilter),
          })
        }
      >
        <SelectTrigger
          className="h-8 w-[172px] text-[13px]"
          aria-label="Filter by review state"
        >
          <SelectValue placeholder="Review" />
        </SelectTrigger>
        <SelectContent>
          <SelectGroup>
            <SelectItem value={ALL}>All reviews</SelectItem>
            {(filters?.reviews ?? []).map((option) => (
              <SelectItem key={option.value} value={option.value}>
                <FacetItem option={option} />
              </SelectItem>
            ))}
          </SelectGroup>
        </SelectContent>
      </Select>

      <Select
        value={params.checks ?? ALL}
        onValueChange={(value) =>
          onFilterChange({
            checks: value === ALL ? undefined : (value as PullRequestChecks["state"]),
          })
        }
      >
        <SelectTrigger
          className="h-8 w-[150px] text-[13px]"
          aria-label="Filter by checks"
        >
          <SelectValue placeholder="Checks" />
        </SelectTrigger>
        <SelectContent>
          <SelectGroup>
            <SelectItem value={ALL}>All checks</SelectItem>
            {(filters?.checks ?? []).map((option) => (
              <SelectItem key={option.value} value={option.value}>
                <FacetItem option={option} />
              </SelectItem>
            ))}
          </SelectGroup>
        </SelectContent>
      </Select>

      <Select
        value={params.sort ?? "updated_desc"}
        onValueChange={(value) => onFilterChange({ sort: value as PullRequestSort })}
      >
        <SelectTrigger
          className="h-8 w-[164px] text-[13px]"
          aria-label="Sort pull requests"
        >
          <SelectValue placeholder="Sort" />
        </SelectTrigger>
        <SelectContent>
          <SelectGroup>
            {SORTS.map((option) => (
              <SelectItem key={option.value} value={option.value}>
                {option.label}
              </SelectItem>
            ))}
          </SelectGroup>
        </SelectContent>
      </Select>

      <div className="flex items-center gap-1.5 px-1">
        <Checkbox
          id="pr-include-drafts"
          checked={params.includeDrafts ?? false}
          onCheckedChange={(checked) =>
            onFilterChange({ includeDrafts: checked === true ? true : undefined })
          }
          aria-label="Include draft pull requests"
        />
        <label
          htmlFor="pr-include-drafts"
          className="cursor-pointer select-none text-[13px] text-muted-foreground"
        >
          Drafts
        </label>
      </div>

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
