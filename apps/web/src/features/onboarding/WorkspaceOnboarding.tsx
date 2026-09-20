/**
 * The workspace gate.
 *
 * Rendered instead of the app shell for a signed-in account that belongs to no
 * workspace: it offers to create one, and otherwise explains — honestly — that
 * only a workspace admin can let the account in.
 */

import { useState, type FormEvent } from "react"
import { AlertTriangle, GitPullRequest, RotateCw } from "lucide-react"

import type { MeResponse } from "@/api/contract"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import type { WorkspaceOnboardingController } from "./lib/useWorkspaceOnboarding"

/**
 * Shown while `/me` is still in flight: without the account there is no way to
 * tell the gate from the shell, so neither is rendered yet.
 */
export function WorkspaceGateLoading() {
  return (
    <div className="grid min-h-screen place-items-center bg-background px-4">
      <div
        role="status"
        aria-live="polite"
        className="flex items-center gap-2.5 text-muted-foreground"
      >
        <span className="grid size-7 place-items-center rounded-md bg-accent text-accent-foreground shadow-sm">
          <GitPullRequest className="size-4" />
        </span>
        <span className="text-[13px] font-medium">Loading your account…</span>
      </div>
    </div>
  )
}

export interface WorkspaceOnboardingProps {
  /** The signed-in account, which `/me` reports belongs to no workspace. */
  account: MeResponse
  /** Gate state and actions; `workspace` closes the gate once `/me` reports one. */
  onboarding: WorkspaceOnboardingController
}

export function WorkspaceOnboarding({
  account,
  onboarding,
}: WorkspaceOnboardingProps) {
  const { status, create, refresh, error, isCreating } = onboarding
  const [name, setName] = useState(() => `${account.handle}'s workspace`)
  const isBusy = status !== "idle"

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    create(name)
  }

  return (
    <div className="grid min-h-screen place-items-center bg-background px-4 py-10">
      <div className="flex w-full max-w-lg flex-col gap-5">
        <div className="flex items-center gap-2.5">
          <span className="grid size-7 shrink-0 place-items-center rounded-md bg-accent text-accent-foreground shadow-sm">
            <GitPullRequest className="size-4" />
          </span>
          <span className="text-[15px] font-semibold tracking-tight">slopolis</span>
        </div>

        <Card>
          <CardHeader className="gap-2">
            <CardTitle className="text-base">Create or join a workspace</CardTitle>
            <CardDescription>
              Signed in as{" "}
              <span className="font-mono text-foreground">@{account.handle}</span>.
              This account does not belong to a workspace yet, and slopolis only
              reviews repositories that belong to one.
            </CardDescription>
          </CardHeader>

          <CardContent className="flex flex-col gap-6">
            <form className="flex flex-col gap-2" onSubmit={handleSubmit}>
              <Label htmlFor="workspace-name">Workspace name</Label>
              <div className="flex items-center gap-2">
                <Input
                  id="workspace-name"
                  name="workspaceName"
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                  autoComplete="off"
                  autoFocus
                  disabled={isBusy}
                  aria-invalid={error ? true : undefined}
                  aria-describedby={error ? "workspace-name-error" : undefined}
                />
                <Button type="submit" disabled={isBusy}>
                  {isCreating ? "Creating…" : "Create workspace"}
                </Button>
              </div>
              {error ? (
                <p
                  id="workspace-name-error"
                  role="alert"
                  className="flex items-center gap-1.5 text-xs text-destructive"
                >
                  <AlertTriangle className="size-3.5 shrink-0" />
                  {error}
                </p>
              ) : (
                <p className="text-xs text-muted-foreground">
                  Pick a name for your team's workspace.
                </p>
              )}
            </form>

            <div className="flex flex-col gap-2 border-t border-border pt-5">
              <h2 className="text-[13px] font-medium">
                Joining an existing workspace?
              </h2>
              <p className="text-xs text-muted-foreground">
                A workspace admin has to invite @{account.handle} — signing in is
                not enough, and there is nothing to accept here until that
                invitation exists. Check again once they have sent it.
              </p>
              <div className="pt-0.5">
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={refresh}
                  disabled={isBusy}
                >
                  <RotateCw data-icon="inline-start" />
                  {status === "checking" ? "Checking…" : "Check again"}
                </Button>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
