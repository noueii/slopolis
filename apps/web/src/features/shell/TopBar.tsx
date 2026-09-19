import { Bell, Menu, Plus } from "lucide-react"

import { isMockModeEnabled, type MockScenario } from "@/api/client"
import type { UserRef } from "@/api/contract"
import { Button } from "@/components/ui/button"
import { MockDataMenu } from "./MockDataMenu"
import { UserMenu } from "./UserMenu"
import { navItemById, type NavId } from "./nav"

export interface TopBarProps {
  active: NavId
  user: UserRef | null
  onOpenNav: () => void
  onNavigate: (id: NavId) => void
  onNewReview: () => void
  scenario: MockScenario
  onScenarioChange: (scenario: MockScenario) => void
}

export function TopBar({
  active,
  user,
  onOpenNav,
  onNavigate,
  onNewReview,
  scenario,
  onScenarioChange,
}: TopBarProps) {
  const current = navItemById(active)

  return (
    <header className="sticky top-0 z-30 flex h-14 shrink-0 items-center gap-3 border-b border-border bg-background/80 px-4 backdrop-blur supports-[backdrop-filter]:bg-background/70 lg:px-6">
      <Button
        variant="ghost"
        size="icon"
        className="lg:hidden"
        aria-label="Open navigation"
        onClick={onOpenNav}
      >
        <Menu />
      </Button>

      <nav aria-label="Breadcrumb" className="flex min-w-0 items-center gap-1.5">
        <span className="hidden font-mono text-xs text-muted-foreground sm:inline">
          slopolis
        </span>
        <span className="hidden text-muted-foreground/50 sm:inline">/</span>
        <span className="truncate text-[13px] font-medium text-foreground">
          {current.label}
        </span>
      </nav>

      <div className="ml-auto flex items-center gap-2">
        {isMockModeEnabled() ? (
          <MockDataMenu
            scenario={scenario}
            onScenarioChange={onScenarioChange}
          />
        ) : null}
        <Button size="sm" className="h-8" onClick={onNewReview}>
          <Plus data-icon="inline-start" />
          <span className="hidden sm:inline">New review</span>
        </Button>
        <Button
          variant="ghost"
          size="icon"
          className="relative hidden sm:inline-flex"
          aria-label="Notifications"
        >
          <Bell />
          <span className="absolute right-2 top-2 size-1.5 rounded-full bg-accent" />
        </Button>
        <UserMenu user={user} />
      </div>
    </header>
  )
}
