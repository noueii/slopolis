import { Check, ChevronDown, GitPullRequest, Plus } from "lucide-react"

import { cn } from "@/lib/utils"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { NAV_GROUPS, visibleNavItems, type NavId } from "./nav"

export interface SidebarProps {
  active: NavId
  onNavigate: (id: NavId) => void
  isAdmin: boolean
  className?: string
}

const WORKSPACES = [
  { id: "acme-labs", name: "acme-labs", hint: "self-hosted" },
  { id: "orbit-labs", name: "orbit-labs", hint: "connected" },
]

export function Sidebar({ active, onNavigate, isAdmin, className }: SidebarProps) {
  const items = visibleNavItems(isAdmin)

  return (
    <div className={cn("flex h-full flex-col bg-sidebar", className)}>
      <div className="flex flex-col gap-3 px-4 pb-4 pt-5">
        <div className="flex items-center gap-2.5">
          <span className="grid size-7 shrink-0 place-items-center rounded-md bg-accent text-accent-foreground shadow-sm">
            <GitPullRequest className="size-4" />
          </span>
          <span className="text-[15px] font-semibold tracking-tight text-sidebar-foreground">
            slopolis
          </span>
        </div>

        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button
              type="button"
              className="flex h-10 w-full items-center gap-2.5 rounded-md border border-sidebar-border bg-background/70 px-2.5 text-left transition-colors hover:bg-background"
            >
              <span className="grid size-6 shrink-0 place-items-center rounded bg-accent/15 font-mono text-2xs font-semibold uppercase text-accent">
                ac
              </span>
              <span className="flex min-w-0 flex-col">
                <span className="truncate text-[13px] font-medium text-sidebar-foreground">
                  acme-labs
                </span>
                <span className="truncate font-mono text-[10px] uppercase tracking-wider text-sidebar-muted">
                  self-hosted
                </span>
              </span>
              <ChevronDown className="ml-auto size-3.5 shrink-0 text-sidebar-muted" />
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start" className="w-[224px]">
            <DropdownMenuLabel className="text-2xs uppercase tracking-widest text-muted-foreground">
              Workspaces
            </DropdownMenuLabel>
            {WORKSPACES.map((workspace) => (
              <DropdownMenuItem key={workspace.id} className="gap-2">
                <span className="grid size-5 place-items-center rounded bg-muted font-mono text-[9px] font-semibold uppercase text-muted-foreground">
                  {workspace.name.slice(0, 2)}
                </span>
                <span className="flex flex-col">
                  <span className="text-[13px] font-medium">{workspace.name}</span>
                  <span className="text-2xs text-muted-foreground">
                    {workspace.hint}
                  </span>
                </span>
                {workspace.id === "acme-labs" ? (
                  <Check className="ml-auto size-3.5 text-accent" />
                ) : null}
              </DropdownMenuItem>
            ))}
            <DropdownMenuSeparator />
            <DropdownMenuItem className="gap-2 text-muted-foreground">
              <Plus className="size-3.5" />
              <span className="text-[13px]">Connect a workspace</span>
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>

      <nav className="flex flex-1 flex-col gap-5 overflow-y-auto scrollbar-thin px-2 pb-4">
        {NAV_GROUPS.map((group) => {
          const groupItems = items.filter((item) => item.group === group)
          if (groupItems.length === 0) return null
          return (
            <div key={group} className="flex flex-col gap-1">
              <p className="px-2.5 pb-1 text-2xs font-semibold uppercase tracking-widest text-sidebar-muted">
                {group}
              </p>
              {groupItems.map((item) => {
                const isActive = item.id === active
                return (
                  <button
                    key={item.id}
                    type="button"
                    onClick={() => onNavigate(item.id)}
                    aria-current={isActive ? "page" : undefined}
                    className={cn(
                      "group relative flex h-9 w-full items-center gap-2.5 rounded-md px-2.5 text-left text-[13px] font-medium transition-colors focus-visible:ring-2 focus-visible:ring-ring",
                      isActive
                        ? "bg-sidebar-accent text-sidebar-foreground"
                        : "text-sidebar-muted hover:bg-sidebar-accent/60 hover:text-sidebar-foreground",
                    )}
                  >
                    {isActive ? (
                      <span className="absolute left-0 top-1/2 h-4 w-0.5 -translate-y-1/2 rounded-full bg-accent" />
                    ) : null}
                    <item.icon className="size-4 shrink-0" />
                    <span className="truncate">{item.label}</span>
                  </button>
                )
              })}
            </div>
          )
        })}
      </nav>

      <div className="flex flex-col gap-1.5 border-t border-sidebar-border px-4 py-4">
        <div className="flex items-center gap-2">
          <span className="relative flex size-2">
            <span className="absolute inline-flex size-full animate-ping rounded-full bg-success/50" />
            <span className="relative inline-flex size-2 rounded-full bg-success" />
          </span>
          <span className="text-2xs font-medium text-sidebar-foreground">
            All systems nominal
          </span>
        </div>
        <p className="font-mono text-[10px] uppercase tracking-wider text-sidebar-muted">
          v0.1 · self-hosted · worker 03/03
        </p>
      </div>
    </div>
  )
}
