/**
 * The caps form's model (spec 10.10).
 *
 * A cap is edited as text, not as a number: the API's `null` and a blank field
 * have to mean the same thing ("unlimited"), while an `<input type="number">`
 * silently discards a non-numeric keystroke — which would turn a typo into what
 * looks like a cleared cap instead of telling the admin about it.
 */

import type { WorkspaceSettings, WorkspaceSettingsUpdate } from "@/api/contract"

/** The four caps, wire-named, in the order the screen shows them. */
export const CAP_KEYS = [
  "maxConcurrentSessions",
  "maxSessionsPerUserPerDay",
  "maxTargetsPerRepo",
  "maxTargetsPerInstallation",
] as const

export type CapKey = (typeof CAP_KEYS)[number]

/** What the admin has typed, per cap; the empty string is "unlimited". */
export type CapDraft = Record<CapKey, string>

const POSITIVE_INTEGER = /^\d+$/

const INVALID_CAP =
  "Enter a whole number of 1 or more, or leave it blank for unlimited."

/** Rebuild the form from what the server stores; `null` becomes a blank field. */
export function draftFrom(settings: WorkspaceSettings): CapDraft {
  return {
    maxConcurrentSessions:
      settings.maxConcurrentSessions === null
        ? ""
        : String(settings.maxConcurrentSessions),
    maxSessionsPerUserPerDay:
      settings.maxSessionsPerUserPerDay === null
        ? ""
        : String(settings.maxSessionsPerUserPerDay),
    maxTargetsPerRepo:
      settings.maxTargetsPerRepo === null
        ? ""
        : String(settings.maxTargetsPerRepo),
    maxTargetsPerInstallation:
      settings.maxTargetsPerInstallation === null
        ? ""
        : String(settings.maxTargetsPerInstallation),
  }
}

/** The cap a field holds, or `null` when it is left blank ("unlimited"). */
function capValue(value: string): number | null {
  const trimmed = value.trim()
  return trimmed === "" ? null : Number(trimmed)
}

/**
 * The refusal for one field, or `null` when it is blank or an integer >= 1.
 * Checked here rather than at the API so an invalid cap never leaves the form.
 */
export function capError(value: string): string | null {
  const trimmed = value.trim()
  if (trimmed === "") return null
  if (!POSITIVE_INTEGER.test(trimmed) || Number(trimmed) < 1) return INVALID_CAP
  return null
}

/** Every field's refusal, keyed by cap; empty when the form is valid. */
export function capErrors(draft: CapDraft): Partial<Record<CapKey, string>> {
  const errors: Partial<Record<CapKey, string>> = {}
  for (const key of CAP_KEYS) {
    const error = capError(draft[key])
    if (error !== null) errors[key] = error
  }
  return errors
}

/**
 * Only the caps the admin actually changed — a cleared field sends `null`, so
 * the server can tell "clear this cap" from "leave it alone".
 */
export function changedCaps(
  draft: CapDraft,
  settings: WorkspaceSettings,
): WorkspaceSettingsUpdate {
  const patch: WorkspaceSettingsUpdate = {}
  for (const key of CAP_KEYS) {
    const next = capValue(draft[key])
    if (next !== settings[key]) patch[key] = next
  }
  return patch
}
