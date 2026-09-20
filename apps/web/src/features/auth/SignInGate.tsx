/**
 * The sign-in gate.
 *
 * Rendered instead of the app shell when `/api/me` reports no account: every
 * screen behind the shell is workspace-scoped and answers 401 or 409 without a
 * session, so the app sends the browser to GitHub through
 * `GET /api/auth/github/login` and lets the API redirect on.
 *
 * A tab that has already been to GitHub once and came back unsigned is not sent
 * again — that would bounce between the consent screen and this gate forever —
 * it gets an explicit retry instead.
 */

import { useCallback, useEffect, useRef, useState } from "react"
import { GitPullRequest, RotateCw } from "lucide-react"

import { beginSignIn, signInAttempted } from "@/api/client"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"

const RETURNED_UNSIGNED =
  "GitHub did not sign this browser in. Try again when you are ready."

export function SignInGate() {
  const [problem, setProblem] = useState<string | null>(() =>
    signInAttempted() ? RETURNED_UNSIGNED : null,
  )
  const started = useRef(false)

  const attempt = useCallback(() => {
    setProblem(null)
    void beginSignIn().then((outcome) => {
      if (!outcome.started) setProblem(outcome.message)
    })
  }, [])

  useEffect(() => {
    // One attempt per mount: StrictMode double-invokes effects, and a second
    // probe would race the navigation.
    if (started.current) return
    started.current = true
    if (problem === null) attempt()
  }, [attempt, problem])

  return (
    <div className="grid min-h-screen place-items-center bg-background px-4 py-10">
      <div className="flex w-full max-w-lg flex-col gap-5">
        <div className="flex items-center gap-2.5">
          <span className="grid size-7 shrink-0 place-items-center rounded-md bg-accent text-accent-foreground shadow-sm">
            <GitPullRequest className="size-4" />
          </span>
          <span className="text-[15px] font-semibold tracking-tight">slopolis</span>
        </div>

        {problem === null ? (
          <div
            role="status"
            aria-live="polite"
            className="flex items-center gap-2.5 text-muted-foreground"
          >
            <span className="text-[13px] font-medium">
              Taking you to GitHub to sign in…
            </span>
          </div>
        ) : (
          <Card>
            <CardHeader className="gap-2">
              <CardTitle className="text-base">Sign in with GitHub</CardTitle>
              <CardDescription>{problem}</CardDescription>
            </CardHeader>
            <CardContent>
              <Button size="sm" onClick={attempt}>
                <RotateCw data-icon="inline-start" />
                Try again
              </Button>
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  )
}
