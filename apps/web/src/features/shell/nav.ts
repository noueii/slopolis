import {
  BookMarked,
  GitPullRequest,
  KeyRound,
  LayoutDashboard,
  LayoutList,
  PlugZap,
  Settings,
  SquarePen,
  Waypoints,
  Workflow,
  type LucideIcon,
} from "lucide-react"

export type NavId =
  | "dashboard"
  | "sessions"
  | "new-review"
  | "repositories"
  | "providers"
  | "templates"
  | "usage"
  | "settings"

export type NavGroup = "Review" | "Configure" | "Observe" | "System"

export interface NavItem {
  id: NavId
  label: string
  icon: LucideIcon
  group: NavGroup
  description: string
}

export const NAV_ITEMS: NavItem[] = [
  {
    id: "dashboard",
    label: "Dashboard",
    icon: LayoutDashboard,
    group: "Review",
    description: "Start a review and watch what is running across your repositories.",
  },
  {
    id: "sessions",
    label: "Sessions",
    icon: LayoutList,
    group: "Review",
    description: "Every review session, its targets, status, and cost.",
  },
  {
    id: "new-review",
    label: "New Review",
    icon: SquarePen,
    group: "Review",
    description: "Paste PR links, add an optional prompt, and run a review.",
  },
  {
    id: "repositories",
    label: "Repositories",
    icon: BookMarked,
    group: "Configure",
    description: "Repositories covered by the GitHub App installation.",
  },
  {
    id: "providers",
    label: "Providers & Models",
    icon: KeyRound,
    group: "Configure",
    description: "Provider credentials and the model assigned to each role.",
  },
  {
    id: "templates",
    label: "Review templates",
    icon: Workflow,
    group: "Configure",
    description:
      "Define review harnesses: an orchestrator, its sub-agents, and the rules they enforce.",
  },
  {
    id: "usage",
    label: "Usage",
    icon: Waypoints,
    group: "Observe",
    description: "Tokens and cost over time, by model, repo, and user.",
  },
  {
    id: "settings",
    label: "Settings",
    icon: Settings,
    group: "System",
    description: "Workspace policy, caps, and access control.",
  },
]

export const NAV_GROUPS: NavGroup[] = ["Review", "Configure", "Observe", "System"]

export function navItemById(id: NavId): NavItem {
  return NAV_ITEMS.find((item) => item.id === id) ?? NAV_ITEMS[0]
}

/** Exported for convenience so screens can reference the brand marks. */
export const BRAND_ICON: LucideIcon = GitPullRequest
export const PROVIDER_ICON: LucideIcon = PlugZap
