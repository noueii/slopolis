import { useState, type ReactNode } from "react"

import {
  getMockScenario,
  setMockScenario,
  type MockScenario,
} from "@/api/client"
import { AppShell } from "@/features/shell/AppShell"
import { navItemById, type NavId } from "@/features/shell/nav"
import { ComingSoonScreen } from "@/features/placeholder/ComingSoonScreen"
import { DashboardScreen } from "@/features/dashboard/DashboardScreen"
import { SessionDetailStub } from "@/features/sessions/SessionDetailStub"
import { SessionsScreen } from "@/features/sessions/SessionsScreen"

export default function App() {
  const [nav, setNav] = useState<NavId>("dashboard")
  const [detailId, setDetailId] = useState<string | null>(null)
  const [scenario, setScenario] = useState<MockScenario>(getMockScenario())

  const handleNavigate = (id: NavId) => {
    setDetailId(null)
    setNav(id)
  }

  const handleScenarioChange = (next: MockScenario) => {
    setMockScenario(next)
    setScenario(next)
  }

  let content: ReactNode
  if (detailId) {
    content = (
      <SessionDetailStub
        sessionId={detailId}
        onBack={() => setDetailId(null)}
      />
    )
  } else if (nav === "dashboard" || nav === "new-review") {
    content = (
      <DashboardScreen
        scenario={scenario}
        onOpenSession={setDetailId}
        autoFocusComposer={nav === "new-review"}
      />
    )
  } else if (nav === "sessions") {
    content = (
      <SessionsScreen
        onOpenSession={setDetailId}
        onNewReview={() => handleNavigate("new-review")}
        scenario={scenario}
      />
    )
  } else {
    content = <ComingSoonScreen item={navItemById(nav)} />
  }

  return (
    <AppShell
      active={nav}
      onNavigate={handleNavigate}
      scenario={scenario}
      onScenarioChange={handleScenarioChange}
    >
      {content}
    </AppShell>
  )
}
