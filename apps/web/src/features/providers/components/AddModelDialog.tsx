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

export interface AddModelDialogProps {
  onOpenChange: (open: boolean) => void
  onCreated: () => void
}

export function AddModelDialog({
  onOpenChange,
  onCreated,
}: AddModelDialogProps) {
  const [modelId, setModelId] = useState("")
  const [provider, setProvider] = useState("")
  const [displayName, setDisplayName] = useState("")
  const { pending, error, run } = useAdminMutation()

  const incomplete = modelId.trim() === "" || provider.trim() === ""

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const saved = await run(async () => {
      await api.addCatalogModel({
        modelId: modelId.trim(),
        provider: provider.trim(),
        displayName: displayName.trim() === "" ? null : displayName.trim(),
      })
    }, "The model could not be added.")
    if (!saved) return
    onCreated()
    onOpenChange(false)
  }

  return (
    <Dialog open onOpenChange={onOpenChange}>
      <DialogContent className="max-w-[440px]">
        <DialogHeader>
          <DialogTitle>Add model</DialogTitle>
          <DialogDescription>
            For a model a provider does not list. Manual models survive deleting
            the credential they were added for; imported ones do not.
          </DialogDescription>
        </DialogHeader>

        <form className="flex flex-col gap-4" onSubmit={submit}>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="model-id">Model ID</Label>
            <Input
              id="model-id"
              value={modelId}
              placeholder="gpt-4o"
              autoComplete="off"
              onChange={(event) => setModelId(event.target.value)}
            />
            <p className="text-2xs text-muted-foreground">
              Exactly as the provider expects it, including any vendor prefix.
            </p>
          </div>

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="model-provider">Provider</Label>
            <Input
              id="model-provider"
              value={provider}
              placeholder="litellm"
              autoComplete="off"
              onChange={(event) => setProvider(event.target.value)}
            />
          </div>

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="model-display-name">Display name (optional)</Label>
            <Input
              id="model-display-name"
              value={displayName}
              autoComplete="off"
              onChange={(event) => setDisplayName(event.target.value)}
            />
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
              {pending ? "Adding…" : "Add model"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
