import { useState } from "react"
import { CheckCircle2, KeyRound, Pencil, Plus, Radio, Trash2 } from "lucide-react"

import { api } from "@/api/client"
import type { ProviderCredential, ProviderTestResult } from "@/api/contract"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { formatRelativeTime } from "@/features/sessions/lib/format"
import { adminErrorMessage } from "../lib/errors"
import { useAdminMutation } from "../lib/useProviders"
import { AddCredentialDialog } from "./AddCredentialDialog"
import { DeleteConfirmDialog } from "./DeleteConfirmDialog"
import { EditCredentialDialog } from "./EditCredentialDialog"

export interface ProviderCredentialsSectionProps {
  credentials: ProviderCredential[]
  /** How many catalog rows each credential imported — they go with it. */
  importedByCredential: Record<string, number>
  onChanged: () => void
}

/** Deleting a credential cascades to the rows it imported (spec 10.2). */
function describeCredentialDeletion(importedModels: number): string {
  if (importedModels === 0) {
    return "The stored key is destroyed. Models added by hand survive, and roles pointing at a deleted model fall back to auto."
  }
  const models =
    importedModels === 1
      ? "the 1 model imported through it"
      : `the ${importedModels} models imported through it`
  return `The stored key is destroyed, along with ${models}. Models added by hand survive, and roles pointing at a deleted model fall back to auto.`
}

export function ProviderCredentialsSection({
  credentials,
  importedByCredential,
  onChanged,
}: ProviderCredentialsSectionProps) {
  const [adding, setAdding] = useState(false)
  const [editing, setEditing] = useState<ProviderCredential | null>(null)
  const [deleting, setDeleting] = useState<ProviderCredential | null>(null)
  const [testingId, setTestingId] = useState<string | null>(null)
  const [results, setResults] = useState<Record<string, ProviderTestResult>>({})
  const [testError, setTestError] = useState<{
    id: string
    message: string
  } | null>(null)
  const deletion = useAdminMutation()

  async function testConnection(credential: ProviderCredential) {
    setTestingId(credential.id)
    setTestError(null)
    try {
      const result = await api.testProvider(credential.id)
      setResults((previous) => ({ ...previous, [credential.id]: result }))
      onChanged()
    } catch (error) {
      setTestError({
        id: credential.id,
        message: adminErrorMessage(
          error,
          "The connection test could not be run.",
        ),
      })
    } finally {
      setTestingId(null)
    }
  }

  async function confirmDelete() {
    if (deleting === null) return
    const removed = await deletion.run(async () => {
      await api.deleteProvider(deleting.id)
    }, "The credential could not be deleted.")
    if (!removed) return
    setDeleting(null)
    onChanged()
  }

  return (
    <section
      aria-label="Provider credentials"
      className="flex flex-col overflow-hidden rounded-lg border border-border bg-card"
    >
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-border px-4 py-3">
        <div className="flex flex-col gap-0.5">
          <h2 className="text-sm font-semibold tracking-tight">
            Provider credentials
          </h2>
          <p className="text-2xs text-muted-foreground">
            Encrypted in the workspace vault. Only the last four characters of a
            key are ever readable.
          </p>
        </div>
        <Button size="sm" onClick={() => setAdding(true)}>
          <Plus data-icon="inline-start" />
          Add credential
        </Button>
      </header>

      {credentials.length === 0 ? (
        <div className="flex flex-col items-center gap-3 px-6 py-12 text-center">
          <span className="grid size-11 place-items-center rounded-full border border-border bg-muted text-muted-foreground">
            <KeyRound className="size-5" />
          </span>
          <div className="flex flex-col gap-1">
            <h3 className="text-sm font-semibold tracking-tight">
              No credentials yet
            </h3>
            <p className="max-w-md text-sm text-muted-foreground">
              A review needs a model, and a model comes from a provider
              credential — pre-flight refuses to create a session until one
              exists. Add a LiteLLM or OpenAI-compatible key to get started.
            </p>
          </div>
        </div>
      ) : (
        <ul className="flex flex-col">
          {credentials.map((credential) => {
            const result = results[credential.id]
            const status = result?.status ?? credential.lastStatus
            const checkedAt = result?.checkedAt ?? credential.lastCheckedAt
            const testing = testingId === credential.id

            return (
              <li
                key={credential.id}
                aria-label={`Credential ${credential.provider} ending ${credential.keyLast4}`}
                className="flex flex-col gap-2.5 border-b border-border px-4 py-3.5 last:border-b-0"
              >
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="flex min-w-0 flex-col gap-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-sm font-medium">
                        {credential.provider}
                      </span>
                      <Badge
                        variant={credential.enabled ? "secondary" : "outline"}
                      >
                        {credential.enabled ? "Enabled" : "Disabled"}
                      </Badge>
                    </div>
                    <div className="flex flex-wrap items-center gap-2 text-2xs text-muted-foreground">
                      {credential.baseUrl === null ? (
                        <span>provider default endpoint</span>
                      ) : (
                        <span className="truncate font-mono">
                          {credential.baseUrl}
                        </span>
                      )}
                      <span className="text-muted-foreground/50">·</span>
                      <span className="font-mono">
                        ••••{credential.keyLast4}
                      </span>
                      <span className="text-muted-foreground/50">·</span>
                      <span>
                        added {formatRelativeTime(credential.createdAt)}
                      </span>
                    </div>
                  </div>

                  <div className="flex flex-wrap items-center gap-2">
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={testing}
                      onClick={() => void testConnection(credential)}
                    >
                      <Radio data-icon="inline-start" />
                      {testing ? "Testing…" : "Test connection"}
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => setEditing(credential)}
                    >
                      <Pencil data-icon="inline-start" />
                      Edit
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      className="text-destructive hover:text-destructive"
                      onClick={() => setDeleting(credential)}
                    >
                      <Trash2 data-icon="inline-start" />
                      Delete
                    </Button>
                  </div>
                </div>

                <div className="flex flex-wrap items-center gap-2 text-2xs">
                  {status === null || checkedAt === null ? (
                    <span className="text-muted-foreground">Never tested</span>
                  ) : (
                    <>
                      <CheckCircle2
                        className={
                          status === "ok"
                            ? "size-3.5 text-success"
                            : "size-3.5 text-destructive"
                        }
                      />
                      <span className="text-muted-foreground">
                        Last test {status === "ok" ? "passed" : "failed"} ·{" "}
                        {formatRelativeTime(checkedAt)}
                      </span>
                      {result !== undefined && result.detail !== null ? (
                        <span className="text-muted-foreground">
                          · {result.detail}
                        </span>
                      ) : null}
                    </>
                  )}
                </div>

                {testError !== null && testError.id === credential.id ? (
                  <p
                    role="alert"
                    className="text-2xs leading-relaxed text-destructive"
                  >
                    {testError.message}
                  </p>
                ) : null}
              </li>
            )
          })}
        </ul>
      )}

      {adding ? (
        <AddCredentialDialog
          onOpenChange={setAdding}
          onCreated={onChanged}
        />
      ) : null}

      {editing !== null ? (
        <EditCredentialDialog
          credential={editing}
          onOpenChange={() => setEditing(null)}
          onUpdated={onChanged}
        />
      ) : null}

      {deleting !== null ? (
        <DeleteConfirmDialog
          onOpenChange={() => setDeleting(null)}
          title={`Delete ${deleting.provider} credential?`}
          description={describeCredentialDeletion(
            importedByCredential[deleting.id] ?? 0,
          )}
          confirmLabel="Delete credential"
          pending={deletion.pending}
          error={deletion.error}
          onConfirm={() => void confirmDelete()}
        />
      ) : null}
    </section>
  )
}
