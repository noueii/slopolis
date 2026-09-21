import { ChevronDown, LogOut, Settings, User } from "lucide-react"

import type { UserRef } from "@/api/contract"
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuShortcut,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"

function initialsFor(user: UserRef): string {
  const source = (user.name.trim() || user.handle).trim()
  const parts = source.split(/\s+/).filter(Boolean)
  if (parts.length >= 2) {
    return `${parts[0][0]}${parts[1][0]}`.toUpperCase()
  }
  return source.slice(0, 2).toUpperCase()
}

export interface UserMenuProps {
  /** The signed-in account; the shell is only rendered with one. */
  user: UserRef
}

export function UserMenu({ user }: UserMenuProps) {
  const name = user.name
  const handle = `@${user.handle}`
  const role = user.isAdmin ? "Admin" : "Member"

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          className="flex h-9 items-center gap-2 rounded-full border border-transparent pl-0.5 pr-2 transition-colors hover:border-border hover:bg-muted/60 focus-visible:ring-2 focus-visible:ring-ring"
          aria-label="Account menu"
        >
          <Avatar className="size-7 border border-border/70">
            {user.avatarUrl ? (
              <AvatarImage src={user.avatarUrl} alt="" />
            ) : null}
            <AvatarFallback className="bg-foreground text-[10px] font-semibold text-background">
              {initialsFor(user)}
            </AvatarFallback>
          </Avatar>
          <span className="hidden flex-col items-start leading-none md:flex">
            <span className="text-[12px] font-medium">{name}</span>
            <span className="font-mono text-[10px] text-muted-foreground">
              {handle}
            </span>
          </span>
          <ChevronDown className="size-3 text-muted-foreground" />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-[232px]">
        <DropdownMenuLabel className="flex flex-col gap-0.5">
          <span className="text-[13px] font-medium">{name}</span>
          <span className="font-mono text-2xs font-normal text-muted-foreground">
            {`${handle} · ${role}`}
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
