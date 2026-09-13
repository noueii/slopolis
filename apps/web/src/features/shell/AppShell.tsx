import { useState, type ReactNode } from "react"

import type { MockScenario } from "@/api/client"
import {
  Sheet,
  SheetContent,
  SheetTitle,
} from "@/components/ui/sheet"
import { TooltipProvider } from "@/components/ui/tooltip"
import { Sidebar } from "./Sidebar"
import { TopBar } from "./TopBar"
import type { NavId } from "./nav"

export interface AppShellProps {
  active: NavId
  onNavigate: (id: NavId) => void
  scenario: MockScenario
  onScenarioChange: (scenario: MockScenario) => void
  children: ReactNode
}

export function AppShell({
  active,
  onNavigate,
  scenario,
  onScenarioChange,
  children,
}: AppShellProps) {
  const [navOpen, setNavOpen] = useState(false)

  const handleNavigate = (id: NavId) => {
    onNavigate(id)
    setNavOpen(false)
  }

  return (
    <TooltipProvider delayDuration={200}>
      <div className="relative grid h-screen grid-cols-1 overflow-hidden bg-background lg:grid-cols-[248px_1fr]">
        <aside className="hidden border-r border-sidebar-border lg:block">
          <Sidebar active={active} onNavigate={handleNavigate} />
        </aside>

        <Sheet open={navOpen} onOpenChange={setNavOpen}>
          <SheetContent
            side="left"
            className="w-[264px] border-sidebar-border bg-sidebar p-0 sm:max-w-[264px]"
          >
            <SheetTitle className="sr-only">Navigation</SheetTitle>
            <Sidebar active={active} onNavigate={handleNavigate} />
          </SheetContent>
        </Sheet>

        <div className="flex min-w-0 flex-col overflow-hidden">
          <TopBar
            active={active}
            onOpenNav={() => setNavOpen(true)}
            onNavigate={handleNavigate}
            scenario={scenario}
            onScenarioChange={onScenarioChange}
          />
          <main className="flex-1 overflow-y-auto scrollbar-thin">{children}</main>
        </div>
      </div>
    </TooltipProvider>
  )
}
