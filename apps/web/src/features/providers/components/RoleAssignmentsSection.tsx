import { useEffect, useState } from "react"

import { api } from "@/api/client"
import type { CatalogModel, RoleAssignment } from "@/api/contract"
import { Label } from "@/components/ui/label"
import { adminErrorMessage } from "../lib/errors"
import { selectClasses } from "../lib/selectStyles"

export interface RoleAssignmentsSectionProps {
  roles: RoleAssignment[]
  models: CatalogModel[]
  /** Model `auto` resolves to; `null` while the catalog is empty. */
  defaultModelId: string | null
  onChanged: () => void
}

/**
 * Configured roles are dotted keys (`review.security`); the label is only ever
 * for display — the key itself is what is sent back to the API. An unknown role
 * falls back to its own key rather than disappearing.
 */
const ROLE_LABELS: Record<string, string> = {
  review: "Review",
  "harness.orchestrator": "Harness orchestrator",
  "review.fast": "Fast review",
  "review.specialist": "Specialist review",
  "review.security": "Security review",
  "review.tests": "Test review",
}

/** The `auto` option: the absence of an assignment, resolved server-side. */
function autoOptionLabel(defaultModelId: string | null): string {
  return defaultModelId === null
    ? "Auto — no default model yet"
    : `Auto — workspace default (${defaultModelId})`
}

export function RoleAssignmentsSection({
  roles,
  models,
  defaultModelId,
  onChanged,
}: RoleAssignmentsSectionProps) {
  // A just-saved choice is held locally so the select does not snap back to the
  // old model while the write and its reload are in flight; the reloaded list
  // becomes the truth again as soon as the server answers differently.
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const [saving, setSaving] = useState<Record<string, boolean>>({})
  const [failures, setFailures] = useState<Record<string, string>>({})

  const serverAnswer = roles
    .map((assignment) => `${assignment.role}=${assignment.modelId ?? ""}`)
    .join("|")

  useEffect(() => {
    setDrafts({})
  }, [serverAnswer])

  async function save(role: string, value: string) {
    setDrafts((previous) => ({ ...previous, [role]: value }))
    setSaving((previous) => ({ ...previous, [role]: true }))
    setFailures((previous) => {
      const next = { ...previous }
      delete next[role]
      return next
    })
    try {
      // `""` is the Auto option, and auto is the absence of an assignment, so
      // the API is told `null` rather than a sentinel model id.
      await api.setAssignment(role, value === "" ? null : value)
      onChanged()
    } catch (error) {
      setDrafts((previous) => {
        const next = { ...previous }
        delete next[role]
        return next
      })
      setFailures((previous) => ({
        ...previous,
        [role]: adminErrorMessage(
          error,
          "The role assignment could not be saved.",
        ),
      }))
    } finally {
      setSaving((previous) => {
        const next = { ...previous }
        delete next[role]
        return next
      })
    }
  }

  return (
    <section
      aria-label="Role assignments"
      className="flex flex-col overflow-hidden rounded-lg border border-border bg-card"
    >
      <header className="flex flex-col gap-0.5 border-b border-border px-4 py-3">
        <h2 className="text-sm font-semibold tracking-tight">
          Role assignments
        </h2>
        <p className="text-2xs text-muted-foreground">
          Which model each role runs on. Auto follows the workspace default:{" "}
          <span className="font-mono">
            {defaultModelId ?? "none yet — the catalog is empty"}
          </span>
          .
        </p>
      </header>

      {roles.length === 0 ? (
        <p className="px-4 py-6 text-sm text-muted-foreground">
          This workspace has no assignable roles to configure.
        </p>
      ) : (
        <ul className="flex flex-col">
          {roles.map((assignment) => {
            const fieldId = `role-assignment-${assignment.role}`
            const value = drafts[assignment.role] ?? assignment.modelId ?? ""

            return (
              <li
                key={assignment.role}
                className="flex flex-col gap-2 border-b border-border px-4 py-3.5 last:border-b-0 sm:flex-row sm:items-center sm:justify-between"
              >
                <div className="flex flex-col gap-0.5">
                  <Label htmlFor={fieldId} className="text-sm">
                    {ROLE_LABELS[assignment.role] ?? assignment.role}
                  </Label>
                  <span className="font-mono text-2xs text-muted-foreground">
                    {assignment.role}
                  </span>
                </div>

                <div className="flex flex-col gap-1 sm:w-[360px]">
                  <select
                    id={fieldId}
                    className={selectClasses}
                    value={value}
                    disabled={saving[assignment.role] === true}
                    onChange={(event) =>
                      void save(assignment.role, event.target.value)
                    }
                  >
                    <option value="">{autoOptionLabel(defaultModelId)}</option>
                    {models.map((model) => (
                      <option key={model.id} value={model.modelId}>
                        {model.displayName ?? model.modelId} · {model.provider}
                      </option>
                    ))}
                  </select>
                  {saving[assignment.role] === true ? (
                    <span className="text-2xs text-muted-foreground">
                      Saving…
                    </span>
                  ) : null}
                  {failures[assignment.role] !== undefined ? (
                    <span
                      role="alert"
                      className="text-2xs leading-relaxed text-destructive"
                    >
                      {failures[assignment.role]}
                    </span>
                  ) : null}
                </div>
              </li>
            )
          })}
        </ul>
      )}
    </section>
  )
}
