import { useEffect, useState, type FormEvent } from "react"
import { RefreshCw } from "lucide-react"

import { cn } from "@/lib/utils"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { CapField } from "./components/CapField"
import { SettingsError, SettingsSkeleton } from "./components/SettingsStates"
import {
  capErrors,
  changedCaps,
  draftFrom,
  type CapDraft,
  type CapKey,
} from "./lib/caps"
import {
  useWorkspaceSettings,
  useWorkspaceSettingsMutation,
} from "./lib/useWorkspaceSettings"

/** Per-field labels and copy, keyed by cap so the two cannot drift apart. */
const CAP_TEXT: Record<CapKey, { label: string; description: string }> = {
  maxConcurrentSessions: {
    label: "Concurrent sessions",
    description:
      "Sessions queued or running for this workspace at once. Leave blank for unlimited.",
  },
  maxSessionsPerUserPerDay: {
    label: "Sessions per user per day",
    description:
      "Sessions one member may create since 00:00 UTC. Leave blank for unlimited.",
  },
  maxTargetsPerRepo: {
    label: "Targets per repository",
    description:
      "Targets of one repository running at the same time. Leave blank for unlimited.",
  },
  maxTargetsPerInstallation: {
    label: "Targets per installation",
    description:
      "Targets of one GitHub App installation running at the same time. Leave blank for unlimited.",
  },
}

/**
 * The workspace admin surface (spec 10.10): the caps submissions and the queue
 * are held to.
 *
 * Every value here is pre-spawn — a gate the submit path applies before a
 * session exists, or a bound the queue applies to jobs already queued — so
 * nothing on this screen changes what a review does once it runs.
 */
export function SettingsScreen() {
  const { data, status, error, refetch } = useWorkspaceSettings()
  const mutation = useWorkspaceSettingsMutation(refetch)
  const [draft, setDraft] = useState<CapDraft | null>(null)

  // The server is the source of truth: a landed save is re-read, and the form
  // is rebuilt from what came back rather than from what was typed.
  useEffect(() => {
    if (data !== null) setDraft(draftFrom(data))
  }, [data])

  const busy = status === "loading" || mutation.pending
  const loading = status === "loading" && data === null
  const failed = status === "error" && data === null

  const form = data === null ? null : draft ?? draftFrom(data)
  const errors = form === null ? {} : capErrors(form)
  const invalid = Object.keys(errors).length > 0
  // An invalid field has no value to send — `Number("abc")` is NaN — so the
  // patch is only built once every field is a blank or an integer >= 1.
  const patch =
    form !== null && data !== null && !invalid ? changedCaps(form, data) : {}
  const dirty = Object.keys(patch).length > 0

  function handleChange(key: CapKey, value: string) {
    // A new keystroke answers the last save: the confirmation (or refusal) was
    // about the values that are no longer on screen.
    mutation.reset()
    setDraft((previous) =>
      previous === null ? previous : { ...previous, [key]: value },
    )
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!dirty || invalid || mutation.pending) return
    void mutation.save(patch)
  }

  return (
    <div
      className="mx-auto flex w-full max-w-[1180px] animate-fade-up flex-col gap-6 p-6"
      aria-busy={busy}
    >
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div className="flex flex-col gap-1">
          <p className="font-mono text-2xs uppercase tracking-widest text-muted-foreground">
            Workspace
          </p>
          <h1 className="text-xl font-semibold tracking-tight">Settings</h1>
          <p className="max-w-2xl text-sm text-muted-foreground">
            Workspace policy and caps. Every cap is opt-in: a blank field means
            unlimited, so a workspace that sets none submits exactly as it did
            before the caps existed.
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={refetch}>
          <RefreshCw data-icon="inline-start" className={cn(busy && "animate-spin")} />
          Refresh
        </Button>
      </header>

      {failed ? (
        <SettingsError
          message={error ?? "Something went wrong while loading the workspace settings."}
          onRetry={refetch}
        />
      ) : null}

      {loading ? <SettingsSkeleton /> : null}

      {form !== null && !failed ? (
        <form onSubmit={handleSubmit} className="flex flex-col gap-6">
          <Card>
            <CardHeader>
              <CardTitle>Session limits</CardTitle>
              <CardDescription className="max-w-3xl">
                Counted when a review is submitted, before pre-flight runs: a
                submission over either cap is refused outright, so nothing is
                created and no GitHub call is made. These bound concurrency, not
                observed usage.
              </CardDescription>
            </CardHeader>
            <CardContent className="flex flex-col gap-5">
              <CapField
                id="maxConcurrentSessions"
                {...CAP_TEXT.maxConcurrentSessions}
                value={form.maxConcurrentSessions}
                error={errors.maxConcurrentSessions ?? null}
                disabled={mutation.pending}
                onChange={(value) => handleChange("maxConcurrentSessions", value)}
              />
              <CapField
                id="maxSessionsPerUserPerDay"
                {...CAP_TEXT.maxSessionsPerUserPerDay}
                value={form.maxSessionsPerUserPerDay}
                error={errors.maxSessionsPerUserPerDay ?? null}
                disabled={mutation.pending}
                onChange={(value) =>
                  handleChange("maxSessionsPerUserPerDay", value)
                }
              />
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Queue concurrency</CardTitle>
              <CardDescription className="max-w-3xl">
                Applied where jobs run, not where they are queued: how many
                targets of the same repository or installation may run at once. A
                target that finds no free slot waits for one instead of failing,
                so a large submission drains in batches.
              </CardDescription>
            </CardHeader>
            <CardContent className="flex flex-col gap-5">
              <CapField
                id="maxTargetsPerRepo"
                {...CAP_TEXT.maxTargetsPerRepo}
                value={form.maxTargetsPerRepo}
                error={errors.maxTargetsPerRepo ?? null}
                disabled={mutation.pending}
                onChange={(value) => handleChange("maxTargetsPerRepo", value)}
              />
              <CapField
                id="maxTargetsPerInstallation"
                {...CAP_TEXT.maxTargetsPerInstallation}
                value={form.maxTargetsPerInstallation}
                error={errors.maxTargetsPerInstallation ?? null}
                disabled={mutation.pending}
                onChange={(value) =>
                  handleChange("maxTargetsPerInstallation", value)
                }
              />
            </CardContent>
          </Card>

          <div className="flex flex-wrap items-center justify-end gap-3">
            {mutation.error !== null ? (
              <p
                role="alert"
                className="mr-auto text-xs leading-relaxed text-destructive"
              >
                {mutation.error}
              </p>
            ) : null}
            {mutation.saved ? (
              <p role="status" className="mr-auto text-xs text-muted-foreground">
                Settings saved.
              </p>
            ) : null}
            <Button type="submit" size="sm" disabled={!dirty || invalid || mutation.pending}>
              {mutation.pending ? "Saving…" : "Save changes"}
            </Button>
          </div>
        </form>
      ) : null}

      {form !== null && !failed ? (
        <Card>
          <CardHeader>
            <CardTitle>Membership</CardTitle>
            <CardDescription className="max-w-3xl">
              Who belongs to this workspace. The only item here is deferred, so
              nothing on this card changes who can sign in yet.
            </CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-4">
            <div className="flex flex-wrap items-start justify-between gap-3 rounded-md border border-border px-4 py-3">
              <div className="flex flex-col gap-1">
                <h3 className="flex items-center gap-2 text-sm font-medium">
                  Member invitations
                  <Badge variant="outline">Deferred</Badge>
                </h3>
                <p className="max-w-2xl text-xs leading-relaxed text-muted-foreground">
                  Inviting members ships with multi-tenancy, and this version has
                  neither: signing in is not enough to join a workspace, and
                  there is nothing to accept here until that invitation exists.
                  Until then membership is managed outside this screen.
                </p>
              </div>
            </div>
          </CardContent>
        </Card>
      ) : null}
    </div>
  )
}
