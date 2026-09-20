import { useState, type ReactNode } from "react"

import {
  getMockScenario,
  setMockScenario,
  type MockScenario,
} from "@/api/client"
import { navigate, useRoute } from "@/lib/route"
import { SignInGate } from "@/features/auth/SignInGate"
import { AppShell } from "@/features/shell/AppShell"
import { navItemById, type NavId } from "@/features/shell/nav"
import { useCurrentUser } from "@/features/shell/useCurrentUser"
import {
  WorkspaceGateLoading,
  WorkspaceOnboarding,
} from "@/features/onboarding/WorkspaceOnboarding"
import { useWorkspaceOnboarding } from "@/features/onboarding/lib/useWorkspaceOnboarding"
import { ComingSoonScreen } from "@/features/placeholder/ComingSoonScreen"
import { ProvidersScreen } from "@/features/providers/ProvidersScreen"
import { DashboardScreen } from "@/features/dashboard/DashboardScreen"
import { RepositoriesScreen } from "@/features/repositories/RepositoriesScreen"
import { RepositoryDetailScreen } from "@/features/repositories/RepositoryDetailScreen"
import { SessionDetail } from "@/features/sessions/SessionDetail"
import { SessionsScreen } from "@/features/sessions/SessionsScreen"
import { TemplatesScreen } from "@/features/templates/TemplatesScreen"
import { UsageScreen } from "@/features/usage/UsageScreen"

export default function App() {
  const route = useRoute()
  const [scenario, setScenario] = useState<MockScenario>(getMockScenario())
  const [composerFocusNonce, setComposerFocusNonce] = useState(0)
  const currentUser = useCurrentUser()
  const onboarding = useWorkspaceOnboarding(currentUser)
  const { user, workspace, isAdmin, isLoading } = currentUser

  // The address bar owns which screen is up. A detail route keeps its list
  // highlighted as the sidebar's active destination.
  const nav: NavId =
    route.kind === "nav"
      ? route.id
      : route.kind === "session"
        ? "sessions"
        : "repositories"

  const openSession = (sessionId: string) =>
    navigate({ kind: "session", sessionId })

  const handleNavigate = (id: NavId) => navigate({ kind: "nav", id })

  const handleNewReview = () => {
    navigate({ kind: "nav", id: "dashboard" })
    setComposerFocusNonce((nonce) => nonce + 1)
  }

  const handleScenarioChange = (next: MockScenario) => {
    setMockScenario(next)
    setScenario(next)
  }

  // The shell is for accounts only: without one there is nothing to act on (every
  // screen behind it is workspace-scoped), so the sign-in gate takes over first,
  // and a signed-in account without a workspace gets the onboarding gate.
  if (isLoading) return <WorkspaceGateLoading />
  if (!user) return <SignInGate />
  if (!workspace) {
    return <WorkspaceOnboarding account={user} onboarding={onboarding} />
  }

  let content: ReactNode
  if (route.kind === "session") {
    content = (
      <SessionDetail
        sessionId={route.sessionId}
        onBack={() => navigate({ kind: "nav", id: "sessions" })}
      />
    )
  } else if (route.kind === "repository") {
    content = (
      <RepositoryDetailScreen
        fullName={route.fullName}
        onBack={() => navigate({ kind: "nav", id: "repositories" })}
        onOpenSession={openSession}
      />
    )
  } else if (route.id === "dashboard") {
    content = (
      <DashboardScreen
        scenario={scenario}
        onOpenSession={openSession}
        focusComposerNonce={composerFocusNonce}
      />
    )
  } else if (route.id === "sessions") {
    content = (
      <SessionsScreen
        onOpenSession={openSession}
        onNewReview={handleNewReview}
        scenario={scenario}
      />
    )
  } else if (route.id === "repositories") {
    content = (
      <RepositoriesScreen
        onOpenRepository={(fullName) => navigate({ kind: "repository", fullName })}
      />
    )
  } else if (route.id === "usage") {
    content = <UsageScreen onNewReview={handleNewReview} />
  } else if (route.id === "providers") {
    content = <ProvidersScreen />
  } else if (route.id === "templates") {
    content = <TemplatesScreen />
  } else {
    content = <ComingSoonScreen item={navItemById(route.id)} />
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
