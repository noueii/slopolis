import type { RequiredAccess, RepositorySummary } from "@/api/contract"

/** The access pre-flight can demand before a review may be triggered. */
export type AccessRequirement = "read" | "write"

export interface RequiredAccessOption {
  value: RequiredAccess
  /** Short name for the picker and the list badge. */
  label: string
  /** What the rule means for who can trigger a review (spec 10.10). */
  effect: string
}

/**
 * The three rules in the order the picker shows them: the spec rule first, then
 * the loosening and the tightening. The copy is the explanation the detail
 * screen owes the reader — the rule's name alone does not say who may trigger.
 */
export const REQUIRED_ACCESS_OPTIONS: RequiredAccessOption[] = [
  {
    value: "default",
    label: "Default",
    effect:
      "Private repositories need read access; public repositories need write access.",
  },
  {
    value: "read",
    label: "Read",
    effect:
      "Any read access is enough, whether the repository is private or public.",
  },
  {
    value: "write",
    label: "Write",
    effect:
      "Write access is required, whether the repository is private or public.",
  },
]

/**
 * The same copy keyed by rule, for surfaces that render one rule at a time (the
 * list badge) rather than offering a choice.
 */
export const REQUIRED_ACCESS_BY_VALUE = Object.fromEntries(
  REQUIRED_ACCESS_OPTIONS.map((option) => [option.value, option]),
) as Record<RequiredAccess, RequiredAccessOption>

/**
 * The access pre-flight actually demands of a viewer. `default` resolves
 * against the repository's own visibility; the two overrides do not.
 */
export function accessRequirement(
  repository: Pick<RepositorySummary, "private" | "requiredAccess">,
): AccessRequirement {
  if (repository.requiredAccess === "read") return "read"
  if (repository.requiredAccess === "write") return "write"
  return repository.private ? "read" : "write"
}
