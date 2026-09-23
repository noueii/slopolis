import {
  BookMarked,
  GitPullRequest,
  KeyRound,
  LayoutList,
  PlugZap,
  Settings,
  Waypoints,
  Workflow,
  type LucideIcon,
} from "lucide-react"

export type NavId =
  | "pullRequests"
  | "sessions"
  | "repositories"
  | "usage"
  | "providers"
  | "templates"
  | "settings"

export type NavGroup = "Workspace" | "Admin"

export type NavAudience = "user" | "admin"

export interface NavItem {
  id: NavId
  label: string
  icon: LucideIcon
  group: NavGroup
  audience: NavAudience
  description: string
}

export const NAV_ITEMS: NavItem[] = [
  {
    id: "pullRequests",
    label: "Pull requests",
    icon: GitPullRequest,
    group: "Workspace",
    audience: "user",
    description:
      "Open pull requests across your repositories, with what slopolis has reviewed.",
  },
  {
    id: "sessions",
    label: "Sessions",
    icon: LayoutList,
    group: "Workspace",
    audience: "user",
    description: "Every review session, its targets, status, and cost.",
  },
  {
    id: "repositories",
    label: "Repositories",
    icon: BookMarked,
    group: "Workspace",
    audience: "user",
    description: "Connect repositories and control which ones slopolis reviews.",
  },
  {
    id: "usage",
    label: "Usage",
    icon: Waypoints,
    group: "Workspace",
    audience: "user",
    description: "Tokens and cost over time, by model, repo, and user.",
  },
  {
    id: "providers",
    label: "Providers & Models",
    icon: KeyRound,
    group: "Admin",
    audience: "admin",
    description: "Provider credentials and the model assigned to each role.",
  },
  {
    id: "templates",
    label: "Review templates",
    icon: Workflow,
    group: "Admin",
    audience: "admin",
    description:
      "Define review harnesses: an orchestrator, its sub-agents, and the rules they enforce.",
  },
  {
    id: "settings",
    label: "Settings",
    icon: Settings,
    group: "Admin",
    audience: "admin",
    description: "Workspace policy, caps, and access control.",
  },
]

export const NAV_GROUPS: NavGroup[] = ["Workspace", "Admin"]

export function navItemById(id: NavId): NavItem {
  return NAV_ITEMS.find((item) => item.id === id) ?? NAV_ITEMS[0]
}

export function visibleNavItems(isAdmin: boolean): NavItem[] {
  return NAV_ITEMS.filter((item) => item.audience === "user" || isAdmin)
}

/** Exported for convenience so screens can reference the brand marks. */
export const BRAND_ICON: LucideIcon = GitPullRequest
export const PROVIDER_ICON: LucideIcon = PlugZap
