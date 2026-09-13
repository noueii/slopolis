import { ChevronDown, LogOut, Settings, User } from "lucide-react"

import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuShortcut,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"

const CURRENT_USER = {
  name: "Noah Yu",
  handle: "noueii",
  role: "Owner",
  initials: "NY",
}

export function UserMenu() {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          className="flex h-9 items-center gap-2 rounded-full border border-transparent pl-0.5 pr-2 transition-colors hover:border-border hover:bg-muted/60 focus-visible:ring-2 focus-visible:ring-ring"
          aria-label="Account menu"
        >
          <Avatar className="size-7 border border-border/70">
            <AvatarFallback className="bg-foreground text-[10px] font-semibold text-background">
              {CURRENT_USER.initials}
            </AvatarFallback>
          </Avatar>
          <span className="hidden flex-col items-start leading-none md:flex">
            <span className="text-[12px] font-medium">{CURRENT_USER.name}</span>
            <span className="font-mono text-[10px] text-muted-foreground">
              @{CURRENT_USER.handle}
            </span>
          </span>
          <ChevronDown className="size-3 text-muted-foreground" />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-[232px]">
        <DropdownMenuLabel className="flex flex-col gap-0.5">
          <span className="text-[13px] font-medium">{CURRENT_USER.name}</span>
          <span className="font-mono text-2xs font-normal text-muted-foreground">
            @{CURRENT_USER.handle} · {CURRENT_USER.role}
          </span>
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuItem className="gap-2">
          <User className="size-3.5 text-muted-foreground" />
          Profile
        </DropdownMenuItem>
        <DropdownMenuItem className="gap-2">
          <Settings className="size-3.5 text-muted-foreground" />
          Workspace settings
          <DropdownMenuShortcut className="font-mono text-[10px]">
            ⌘,
          </DropdownMenuShortcut>
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem className="gap-2 text-destructive focus:text-destructive">
          <LogOut className="size-3.5" />
          Sign out
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
