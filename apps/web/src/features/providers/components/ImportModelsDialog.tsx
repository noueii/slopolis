import { useState } from "react"

import { api } from "@/api/client"
import type { ModelImportResponse, ProviderCredential } from "@/api/contract"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Label } from "@/components/ui/label"
import { selectClasses } from "../lib/selectStyles"
import { useAdminMutation } from "../lib/useProviders"

export interface ImportModelsDialogProps {
  credentials: ProviderCredential[]
  onOpenChange: (open: boolean) => void
  onImported: () => void
}

/** What the provider answered, in the workspace's terms. */
function describeImport(result: ModelImportResponse): string {
  if (result.imported === 0) {
    return result.items.length === 0
      ? "This credential listed no models."
      : `No new models — the ${result.items.length} it offers were already in the catalog and have been refreshed.`
  }
  return `${result.imported} new model${result.imported === 1 ? "" : "s"} imported.`
}

export function ImportModelsDialog({
  credentials,
  onOpenChange,
  onImported,
}: ImportModelsDialogProps) {
  const [credentialId, setCredentialId] = useState(credentials[0]?.id ?? "")
  const [outcome, setOutcome] = useState<string | null>(null)
  const { pending, error, run } = useAdminMutation()

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const imported = await run(async () => {
      const result = await api.importCatalogModels(credentialId)
      setOutcome(describeImport(result))
    }, "The provider's models could not be imported.")
    if (imported) onImported()
  }

  return (
    <Dialog open onOpenChange={onOpenChange}>
      <DialogContent className="max-w-[440px]">
        <DialogHeader>
          <DialogTitle>Import from provider</DialogTitle>
          <DialogDescription>
            Reads the credential&apos;s model list and adds what the catalog is
            missing; models already here are refreshed instead of duplicated.
          </DialogDescription>
        </DialogHeader>

        <form className="flex flex-col gap-4" onSubmit={submit}>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="import-credential">Provider credential</Label>
            <select
              id="import-credential"
              className={selectClasses}
              value={credentialId}
              onChange={(event) => setCredentialId(event.target.value)}
            >
              {credentials.map((credential) => (
                <option key={credential.id} value={credential.id}>
                  {credential.provider} · ••••{credential.keyLast4}
                  {credential.baseUrl !== null ? ` · ${credential.baseUrl}` : ""}
                </option>
              ))}
            </select>
          </div>

          {outcome !== null ? (
            <p role="status" className="text-xs leading-relaxed text-foreground">
              {outcome}
            </p>
          ) : null}

          {error !== null ? (
            <p role="alert" className="text-xs leading-relaxed text-destructive">
              {error}
            </p>
          ) : null}

          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={pending}
              onClick={() => onOpenChange(false)}
            >
              {outcome === null ? "Cancel" : "Close"}
            </Button>
            <Button
              type="submit"
              size="sm"
              disabled={pending || credentialId === ""}
            >
              {pending ? "Importing…" : "Import models"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
