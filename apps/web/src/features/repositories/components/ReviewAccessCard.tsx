import { useState } from "react"

import type { RequiredAccess, RepositorySummary } from "@/api/contract"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import { REQUIRED_ACCESS_OPTIONS, accessRequirement } from "../lib/reviewAccess"

export interface ReviewAccessCardProps {
  repository: RepositorySummary
  pending: boolean
  onSave: (requiredAccess: RequiredAccess) => void
}

/**
 * The repository's own triggering rule (spec 10.10), the sibling of its parked
 * switch. The picker and the sentence under it carry the part the rule's name
 * cannot: who the rule lets trigger, and that pre-flight refuses a trigger that
 * does not meet it by naming what was required.
 */
export function ReviewAccessCard({
  repository,
  pending,
  onSave,
}: ReviewAccessCardProps) {
  const [choice, setChoice] = useState<RequiredAccess>(repository.requiredAccess)
  const [current, setCurrent] = useState<RequiredAccess>(repository.requiredAccess)

  // The screen refetches the row after every save, and the server is the source
  // of truth for what stuck; resync the picker when the row it renders changes.
  if (current !== repository.requiredAccess) {
    setCurrent(repository.requiredAccess)
    setChoice(repository.requiredAccess)
  }

  const requirement = accessRequirement({
    private: repository.private,
    requiredAccess: choice,
  })

  return (
    <section className="flex flex-col gap-3 rounded-lg border border-border bg-card px-4 py-3.5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex flex-col gap-0.5">
          <h2 className="text-sm font-semibold tracking-tight">Review access</h2>
          <p className="max-w-xl text-[13px] leading-relaxed text-muted-foreground">
            What a viewer needs on this repository before they may trigger a
            review of its pull requests.
          </p>
        </div>
        <Button
          type="button"
          size="sm"
          disabled={pending || choice === repository.requiredAccess}
          onClick={() => onSave(choice)}
        >
          {pending ? "Saving…" : "Save"}
        </Button>
      </div>

      <fieldset disabled={pending} className="flex flex-col gap-2">
        <legend className="sr-only">Review access rule</legend>
        {REQUIRED_ACCESS_OPTIONS.map((option) => (
          <label
            key={option.value}
            className={cn(
              "flex cursor-pointer items-start gap-2.5 rounded-lg border px-3 py-2.5 transition-colors",
              choice === option.value
                ? "border-accent/50 bg-accent/5"
                : "border-border hover:bg-muted/40",
            )}
          >
            <input
              type="radio"
              name="review-access"
              value={option.value}
              checked={choice === option.value}
              onChange={() => setChoice(option.value)}
              className="mt-0.5 size-3.5 accent-primary"
            />
            <span className="flex flex-col gap-0.5">
              <span className="text-[13px] font-medium text-foreground">
                {option.label}
              </span>{" "}
              {/* The separator keeps the radio's accessible name from running
                  the rule's name into its explanation. */}
              <span className="text-xs leading-relaxed text-muted-foreground">
                {option.effect}
              </span>
            </span>
          </label>
        ))}
      </fieldset>

      <p className="text-xs leading-relaxed text-muted-foreground">
        Pre-flight refuses a trigger that does not meet the rule, naming the
        requirement:{" "}
        <code className="rounded bg-muted/60 px-1.5 py-0.5 font-mono text-[11px] text-foreground">
          {requirement} access is required on {repository.fullName}
        </code>
      </p>
    </section>
  )
}
