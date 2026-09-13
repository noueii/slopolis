import { useEffect, useRef, useState } from "react"
import { Check, ChevronDown, Globe, Lock, Search } from "lucide-react"

import { cn } from "@/lib/utils"
import type { RepositorySummary } from "@/api/contract"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Input } from "@/components/ui/input"

export interface RepositoryPickerProps {
  repositories: RepositorySummary[] | null
  value: string | null
  onChange: (repo: string | null) => void
  className?: string
}

export function RepositoryPicker({
  repositories,
  value,
  onChange,
  className,
}: RepositoryPickerProps) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState("")
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (!open) return
    const timer = window.setTimeout(() => inputRef.current?.focus(), 0)
    return () => window.clearTimeout(timer)
  }, [open])

  const handleOpenChange = (next: boolean) => {
    setOpen(next)
    if (!next) setQuery("")
  }

  const needle = query.trim().toLowerCase()
  const options = (repositories ?? []).filter((repo) =>
    repo.fullName.toLowerCase().includes(needle),
  )

  const select = (repo: string | null) => {
    onChange(repo)
    setOpen(false)
  }

  return (
    <DropdownMenu open={open} onOpenChange={handleOpenChange}>
      <DropdownMenuTrigger asChild>
        <Button
          variant="outline"
          className={cn("h-9 max-w-[280px] justify-start gap-2 font-normal", className)}
          aria-label="Filter dashboard by repository"
        >
          <Globe data-icon="inline-start" className="text-muted-foreground" />
          <span className="truncate font-mono text-[13px]">
            {value ?? "All repositories"}
          </span>
          <ChevronDown data-icon="inline-end" className="ml-auto opacity-60" />
        </Button>
      </DropdownMenuTrigger>

      <DropdownMenuContent align="end" className="w-[320px] p-1.5">
        <div className="relative px-0.5 pb-1.5 pt-0.5">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input
            ref={inputRef}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => event.stopPropagation()}
            placeholder="Search repositories…"
            aria-label="Search repositories"
            className="h-8 pl-8 text-[13px]"
          />
        </div>
        <DropdownMenuSeparator className="my-1" />
        <DropdownMenuLabel className="text-2xs uppercase tracking-widest text-muted-foreground">
          Repositories
        </DropdownMenuLabel>

        <DropdownMenuItem
          onSelect={() => select(null)}
          className="gap-2 py-2"
        >
          <Globe className="text-muted-foreground" />
          <span className="text-[13px]">All repositories</span>
          {!value ? <Check className="ml-auto text-accent" /> : null}
        </DropdownMenuItem>

        {options.map((repo) => (
          <DropdownMenuItem
            key={repo.id}
            onSelect={() => select(repo.fullName)}
            className="gap-2 py-2"
          >
            {repo.private ? (
              <Lock className="text-muted-foreground" />
            ) : (
              <Globe className="text-muted-foreground" />
            )}
            <span className="min-w-0 flex-1 truncate font-mono text-[12px]">
              {repo.fullName}
            </span>
            <span className="shrink-0 font-mono text-[10px] text-muted-foreground">
              {repo.openPrCount} open
            </span>
            {value === repo.fullName ? (
              <Check className="shrink-0 text-accent" />
            ) : null}
          </DropdownMenuItem>
        ))}

        {options.length === 0 ? (
          <p className="px-2 py-6 text-center text-[13px] text-muted-foreground">
            {repositories === null
              ? "Loading repositories…"
              : "No repositories match."}
          </p>
        ) : null}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
