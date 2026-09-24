/**
 * The app's URL surface.
 *
 * Every screen the shell can show has a path, and the session detail's path is
 * the permalink spec 10.8 requires (`/sessions/{id}`): a link pasted in a PR
 * comment or a chat has to open the session, not the inbox. The app is a
 * single-page shell, so this is not a router library — it is the mapping from
 * `location.pathname` to the screen state the shell already kept in `useState`,
 * plus the one hook that keeps them in step.
 *
 * The browser is the state holder: back/forward work for free because a
 * navigation is a real history entry, and nothing else may own "which screen".
 */

import { useEffect, useSyncExternalStore } from "react"

import type { NavId } from "@/features/shell/nav"

/** Path of each sidebar destination; the compiler enforces full coverage. */
export const NAV_PATHS = {
  pullRequests: "/",
  sessions: "/sessions",
  repositories: "/repositories",
  usage: "/usage",
  providers: "/providers",
  templates: "/templates",
  settings: "/settings",
} as const satisfies Record<NavId, string>

/** The inverse of {@link NAV_PATHS}, derived once so the two cannot drift. */
const NAV_BY_PATH: Record<string, NavId> = Object.fromEntries(
  Object.entries(NAV_PATHS).map(([id, path]) => [path, id as NavId]),
)

const INBOX: Route = { kind: "nav", id: "pullRequests" }

/** The screen the shell renders for a URL. */
export type Route =
  | { kind: "nav"; id: NavId }
  | { kind: "session"; sessionId: string }
  | { kind: "repository"; fullName: string }

/**
 * Map a pathname onto a route, or `null` when nothing owns that path.
 *
 * A repository's path keeps the owner in its own segment
 * (`/repositories/acme/api`) because the full name already reads that way in
 * every other surface.
 */
export function parsePath(pathname: string): Route | null {
  const parts = pathname.split("/").filter((part) => part.length > 0)
  if (parts.length === 0) return INBOX

  const decoded = parts.map((part) => {
    try {
      return decodeURIComponent(part)
    } catch {
      return part
    }
  })
  const path = `/${decoded.join("/")}`

  const nav = NAV_BY_PATH[path]
  if (nav) return { kind: "nav", id: nav }

  if (decoded[0] === "sessions" && decoded.length === 2) {
    return { kind: "session", sessionId: decoded[1] }
  }
  if (decoded[0] === "repositories" && decoded.length === 3) {
    return { kind: "repository", fullName: `${decoded[1]}/${decoded[2]}` }
  }
  return null
}

/** The canonical path for a route (the inverse of {@link parsePath}). */
export function pathFor(route: Route): string {
  switch (route.kind) {
    case "nav":
      return NAV_PATHS[route.id]
    case "session":
      return `/sessions/${encodeURIComponent(route.sessionId)}`
    case "repository": {
      const [owner, ...rest] = route.fullName.split("/")
      return `/repositories/${encodeURIComponent(owner)}/${rest
        .map((part) => encodeURIComponent(part))
        .join("/")}`
    }
  }
}

/**
 * Push a route onto the history and tell the shell about it.
 *
 * `popstate` is dispatched by hand because `pushState` does not fire it — the
 * store below is the only listener, so one event is enough to re-render.
 */
export function navigate(route: Route, options: { replace?: boolean } = {}): void {
  const path = pathFor(route)
  if (path === window.location.pathname) return
  if (options.replace) window.history.replaceState(null, "", path)
  else window.history.pushState(null, "", path)
  window.dispatchEvent(new PopStateEvent("popstate"))
}

function subscribe(onChange: () => void): () => void {
  window.addEventListener("popstate", onChange)
  return () => window.removeEventListener("popstate", onChange)
}

function snapshot(): string {
  return window.location.pathname
}

/**
 * The current route, kept in step with the address bar.
 *
 * A path nothing owns (a stale link, a typo) falls back to the inbox and
 * rewrites the URL, so a reload does not repeat the miss.
 */
export function useRoute(): Route {
  const path = useSyncExternalStore(subscribe, snapshot)
  const route = parsePath(path)

  useEffect(() => {
    if (parsePath(path) === null) navigate(INBOX, { replace: true })
  }, [path])

  return route ?? INBOX
}
