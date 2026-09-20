import { useState } from "react"

import { api } from "@/api/client"
import type { ProviderCredential, ProviderUpdate } from "@/api/contract"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { useAdminMutation } from "../lib/useProviders"

export interface EditCredentialDialogProps {
  credential: ProviderCredential
  onOpenChange: (open: boolean) => void
  onUpdated: () => void
}

export function EditCredentialDialog({
  credential,
  onOpenChange,
  onUpdated,
}: EditCredentialDialogProps) {
  const [baseUrl, setBaseUrl] = useState(credential.baseUrl ?? "")
  const [apiKey, setApiKey] = useState("")
  const [enabled, setEnabled] = useState(credential.enabled)
  const { pending, error, run } = useAdminMutation()

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const patch: ProviderUpdate = {
      baseUrl: baseUrl.trim() === "" ? null : baseUrl.trim(),
      enabled,
    }
    // An empty field means "keep the stored key": the API only receives a new
    // key when one was actually typed.
    if (apiKey !== "") patch.apiKey = apiKey

    const saved = await run(async () => {
      await api.updateProvider(credential.id, patch)
    }, "The credential could not be updated.")
    if (!saved) return
    onUpdated()
    onOpenChange(false)
  }

  return (
    <Dialog open onOpenChange={onOpenChange}>
      <DialogContent className="max-w-[440px]">
        <DialogHeader>
          <DialogTitle>Edit {credential.provider}</DialogTitle>
          <DialogDescription>
            Current key <span className="font-mono">••••{credential.keyLast4}</span>
            . Replacing it is the only way to change the stored secret.
          </DialogDescription>
        </DialogHeader>

        <form className="flex flex-col gap-4" onSubmit={submit}>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="edit-base-url">Base URL</Label>
            <Input
              id="edit-base-url"
              value={baseUrl}
              placeholder="https://llm.example.com/v1"
              autoComplete="off"
              onChange={(event) => setBaseUrl(event.target.value)}
            />
            <p className="text-2xs text-muted-foreground">
              Leave empty for the provider&apos;s default endpoint.
            </p>
          </div>

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="edit-api-key">Replace API key</Label>
            <Input
              id="edit-api-key"
              type="password"
              value={apiKey}
              autoComplete="off"
              placeholder="Leave blank to keep the current key"
              onChange={(event) => setApiKey(event.target.value)}
            />
          </div>

          <div className="flex items-center gap-2.5">
            <Checkbox
              id="edit-enabled"
              checked={enabled}
              onCheckedChange={(value) => setEnabled(value === true)}
            />
            <Label htmlFor="edit-enabled" className="text-sm">
              Enabled
            </Label>
          </div>

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
              Cancel
            </Button>
            <Button type="submit" size="sm" disabled={pending}>
              {pending ? "Saving…" : "Save changes"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
