import { describe, expect, it } from "vitest"

import { NAV_GROUPS, NAV_ITEMS, visibleNavItems } from "./nav"

describe("nav information architecture", () => {
  it("has no dedicated New Review destination", () => {
    const ids: string[] = NAV_ITEMS.map((item) => item.id)
    expect(ids).not.toContain("new-review")
  })

  it("splits navigation into Workspace and Admin audiences", () => {
    expect(NAV_GROUPS).toEqual(["Workspace", "Admin"])

    const workspace = NAV_ITEMS.filter((item) => item.group === "Workspace")
    expect(workspace.map((item) => item.id)).toEqual([
      "dashboard",
      "sessions",
      "repositories",
      "usage",
    ])
    expect(workspace.every((item) => item.audience === "user")).toBe(true)

    const admin = NAV_ITEMS.filter((item) => item.group === "Admin")
    expect(admin.map((item) => item.id)).toEqual([
      "providers",
      "templates",
      "settings",
    ])
    expect(admin.every((item) => item.audience === "admin")).toBe(true)
  })

  it("hides admin items from non-admins", () => {
    const guest = visibleNavItems(false)
    expect(guest).toHaveLength(4)
    expect(guest.every((item) => item.audience === "user")).toBe(true)
    expect(guest.some((item) => item.id === "templates")).toBe(false)

    const admin = visibleNavItems(true)
    expect(admin).toHaveLength(NAV_ITEMS.length)
    expect(admin.some((item) => item.id === "templates")).toBe(true)
  })
})
