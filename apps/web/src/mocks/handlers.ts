/**
 * Aggregated MSW handlers. New features append their handler arrays here.
 * Imported only by the mock bootstrap in `main.tsx`.
 */

import { dashboardHandlers } from "./dashboard"
import { sessionsHandlers } from "./sessions"
import { templatesHandlers } from "./templates"

export const handlers = [
  ...sessionsHandlers,
  ...dashboardHandlers,
  ...templatesHandlers,
]
