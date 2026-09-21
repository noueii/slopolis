import { useState } from "react"

import { api } from "@/api/client"
import { Button } from "@/components/ui/button"
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

export interface AddCredentialDialogProps {
  onOpenChange: (open: boolean) => void
  onCreated: () => void
}

/**
 * Mounted only while the dialog is open, so each opening starts from empty
 * fields — including the key, which is never echoed back once submitted.
 */
export function AddCredentialDialog({
  onOpenChange,
  onCreated,
}: AddCredentialDialogProps) {
  const [provider, setProvider] = useState("litellm")
  const [baseUrl, setBaseUrl] = useState("")
  const [apiKey, setApiKey] = useState("")
  const { pending, error, run } = useAdminMutation()

  const incomplete = provider.trim() === "" || apiKey === ""

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const saved = await run(async () => {
      await api.createProvider({
        provider: provider.trim(),
        baseUrl: baseUrl.trim() === "" ? null : baseUrl.trim(),
        apiKey,
      })
    }, "The credential could not be saved.")
    if (!saved) return
    onCreated()
    onOpenChange(false)
  }

  return (
    <Dialog open onOpenChange={onOpenChange}>
      <DialogContent className="max-w-[440px]">
        <DialogHeader>
          <DialogTitle>Add credential</DialogTitle>
          <DialogDescription>
            LiteLLM is first-class; any OpenAI-compatible endpoint works too by
            giving its base URL.
          </DialogDescription>
        </DialogHeader>

        <form className="flex flex-col gap-4" onSubmit={submit}>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="credential-provider">Provider</Label>
            <Input
              id="credential-provider"
              value={provider}
              placeholder="litellm"
              autoComplete="off"
              onChange={(event) => setProvider(event.target.value)}
            />
          </div>

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="credential-base-url">Base URL (optional)</Label>
            <Input
              id="credential-base-url"
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
            <Label htmlFor="credential-api-key">API key</Label>
            <Input
              id="credential-api-key"
              type="password"
              value={apiKey}
              autoComplete="off"
              onChange={(event) => setApiKey(event.target.value)}
            />
            <p className="text-2xs leading-relaxed text-muted-foreground">
              The key is encrypted into the workspace vault and is never shown
              again — only its last four characters stay visible.
            </p>
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
            <Button type="submit" size="sm" disabled={pending || incomplete}>
              {pending ? "Saving…" : "Save credential"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
