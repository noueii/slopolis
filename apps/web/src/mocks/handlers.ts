/**
 * Aggregated MSW handlers. New features append their handler arrays here.
 * Imported by the in-browser worker and the standalone mock API server.
 */

import { isMockModeEnabled } from "@/api/client"

import { dashboardHandlers } from "./dashboard"
import { meHandlers } from "./me"
import { providersHandlers } from "./providers"
import { runsHandlers } from "./runs"
import { sessionsHandlers } from "./sessions"
import { templatesHandlers } from "./templates"
import { usageHandlers } from "./usage"
import { workspaceHandlers } from "./workspaces"

/** Handlers for features that are backed by the real API. */
export const featureHandlers = [
  ...meHandlers,
  ...workspaceHandlers,
  ...sessionsHandlers,
  ...runsHandlers,
  ...dashboardHandlers,
  ...usageHandlers,
  ...providersHandlers,
]

/**
 * Templates have no Phase-0 backend yet, so their handlers stay available even
 * when `VITE_MOCK=off`: the worker serves them while every other request
 * bypasses to the real API.
 */
export const templatesMockHandlers = [...templatesHandlers]

/** Every handler, used by the standalone mock API server. */
export const handlers = [...featureHandlers, ...templatesMockHandlers]

/** Feature handlers in mock mode; templates-only when talking to the real API. */
export function selectHandlers(mockEnabled: boolean) {
  return mockEnabled ? handlers : templatesMockHandlers
}

/** The handler set the in-browser worker should register for the active mode. */
export function activeHandlers() {
  return selectHandlers(isMockModeEnabled())
}
