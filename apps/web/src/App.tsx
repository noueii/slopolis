import { useState, type ReactNode } from "react"

import {
  getMockScenario,
  setMockScenario,
  type MockScenario,
} from "@/api/client"
import { AppShell } from "@/features/shell/AppShell"
import { navItemById, type NavId } from "@/features/shell/nav"
import { useCurrentUser } from "@/features/shell/useCurrentUser"
import {
  WorkspaceGateLoading,
  WorkspaceOnboarding,
} from "@/features/onboarding/WorkspaceOnboarding"
import { useWorkspaceOnboarding } from "@/features/onboarding/lib/useWorkspaceOnboarding"
import { ComingSoonScreen } from "@/features/placeholder/ComingSoonScreen"
import { DashboardScreen } from "@/features/dashboard/DashboardScreen"
import { RepositoriesScreen } from "@/features/repositories/RepositoriesScreen"
import { RepositoryDetailScreen } from "@/features/repositories/RepositoryDetailScreen"
import { SessionDetail } from "@/features/sessions/SessionDetail"
import { SessionsScreen } from "@/features/sessions/SessionsScreen"
import { TemplatesScreen } from "@/features/templates/TemplatesScreen"

export default function App() {
  const [nav, setNav] = useState<NavId>("dashboard")
  const [detailId, setDetailId] = useState<string | null>(null)
  const [repoFullName, setRepoFullName] = useState<string | null>(null)
  const [scenario, setScenario] = useState<MockScenario>(getMockScenario())
  const [composerFocusNonce, setComposerFocusNonce] = useState(0)
  const currentUser = useCurrentUser()
  const onboarding = useWorkspaceOnboarding(currentUser)
  const { user, workspace, isAdmin, isLoading } = currentUser

  const handleNavigate = (id: NavId) => {
    setDetailId(null)
    setRepoFullName(null)
    setNav(id)
  }

  const handleNewReview = () => {
    setDetailId(null)
    setRepoFullName(null)
    setNav("dashboard")
    setComposerFocusNonce((nonce) => nonce + 1)
  }

  const handleScenarioChange = (next: MockScenario) => {
    setMockScenario(next)
    setScenario(next)
  }

  // A signed-in account without a workspace never sees the shell: the gate is
  // the only screen it can act from. Guests keep the pre-existing shell.
  if (isLoading) return <WorkspaceGateLoading />
  if (user && !workspace) {
    return <WorkspaceOnboarding account={user} onboarding={onboarding} />
  }

  let content: ReactNode
  if (detailId) {
    content = (
      <SessionDetail
        sessionId={detailId}
        onBack={() => setDetailId(null)}
      />
    )
  } else if (nav === "dashboard") {
    content = (
      <DashboardScreen
        scenario={scenario}
        onOpenSession={setDetailId}
        focusComposerNonce={composerFocusNonce}
      />
    )
  } else if (nav === "sessions") {
    content = (
      <SessionsScreen
        onOpenSession={setDetailId}
        onNewReview={handleNewReview}
        scenario={scenario}
      />
    )
  } else if (nav === "repositories") {
    content = repoFullName ? (
      <RepositoryDetailScreen
        fullName={repoFullName}
        onBack={() => setRepoFullName(null)}
        onOpenSession={setDetailId}
      />
    ) : (
      <RepositoriesScreen onOpenRepository={setRepoFullName} />
    )
  } else if (nav === "templates") {
    content = <TemplatesScreen />
  } else {
    content = <ComingSoonScreen item={navItemById(nav)} />
  }

  return (
    <AppShell
      active={nav}
      isAdmin={isAdmin}
      user={user}
      workspace={workspace}
      onNavigate={handleNavigate}
      onNewReview={handleNewReview}
      scenario={scenario}
      onScenarioChange={handleScenarioChange}
    >
      {content}
    </AppShell>
  )
}
