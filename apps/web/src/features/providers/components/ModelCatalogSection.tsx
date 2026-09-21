import { useState } from "react"
import { Boxes, Download, Plus, Trash2 } from "lucide-react"

import { api } from "@/api/client"
import type { CatalogModel, ProviderCredential } from "@/api/contract"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { useAdminMutation } from "../lib/useProviders"
import { AddModelDialog } from "./AddModelDialog"
import { DeleteConfirmDialog } from "./DeleteConfirmDialog"
import { ImportModelsDialog } from "./ImportModelsDialog"

export interface ModelCatalogSectionProps {
  models: CatalogModel[]
  /** The first catalog entry — what `auto` resolves to. */
  defaultModelId: string | null
  credentials: ProviderCredential[]
  onChanged: () => void
}

export function ModelCatalogSection({
  models,
  defaultModelId,
  credentials,
  onChanged,
}: ModelCatalogSectionProps) {
  const [importing, setImporting] = useState(false)
  const [adding, setAdding] = useState(false)
  const [deleting, setDeleting] = useState<CatalogModel | null>(null)
  const deletion = useAdminMutation()

  // `auto` resolves to the catalog's first entry, not to every row that happens
  // to carry the same model id under another provider.
  const defaultRow = models.find((model) => model.modelId === defaultModelId)

  async function confirmDelete() {
    if (deleting === null) return
    const removed = await deletion.run(async () => {
      await api.deleteCatalogModel(deleting.id)
    }, "The model could not be removed from the catalog.")
    if (!removed) return
    setDeleting(null)
    onChanged()
  }

  return (
    <section
      aria-label="Model catalog"
      className="flex flex-col overflow-hidden rounded-lg border border-border bg-card"
    >
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-border px-4 py-3">
        <div className="flex flex-col gap-0.5">
          <h2 className="text-sm font-semibold tracking-tight">
            Model catalog
          </h2>
          <p className="text-2xs text-muted-foreground">
            Roles and the review composer can only pick from models the
            workspace holds. The first entry is the default.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            disabled={credentials.length === 0}
            onClick={() => setImporting(true)}
          >
            <Download data-icon="inline-start" />
            Import from provider
          </Button>
          <Button size="sm" onClick={() => setAdding(true)}>
            <Plus data-icon="inline-start" />
            Add model
          </Button>
        </div>
      </header>

      {models.length === 0 ? (
        <div className="flex flex-col items-center gap-3 px-6 py-12 text-center">
          <span className="grid size-11 place-items-center rounded-full border border-border bg-muted text-muted-foreground">
            <Boxes className="size-5" />
          </span>
          <div className="flex flex-col gap-1">
            <h3 className="text-sm font-semibold tracking-tight">
              The catalog is empty
            </h3>
            <p className="max-w-md text-sm text-muted-foreground">
              Import a credential&apos;s model list, or add a model ID by hand.
              Every role stays on Auto until there is a model to point it at.
            </p>
          </div>
        </div>
      ) : (
        <ul className="flex flex-col">
          {models.map((model) => (
            <li
              key={model.id}
              aria-label={`Model ${model.modelId}`}
              className="flex flex-wrap items-center justify-between gap-3 border-b border-border px-4 py-3 last:border-b-0"
            >
              <div className="flex min-w-0 flex-col gap-1">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-sm font-medium">
                    {model.displayName ?? model.modelId}
                  </span>
                  {model.id === defaultRow?.id ? (
                    <Badge variant="secondary">Default</Badge>
                  ) : null}
                </div>
                <div className="flex flex-wrap items-center gap-2 text-2xs text-muted-foreground">
                  {model.displayName !== null ? (
                    <>
                      <span className="truncate font-mono">{model.modelId}</span>
                      <span className="text-muted-foreground/50">·</span>
                    </>
                  ) : null}
                  <span>{model.provider}</span>
                  <span className="text-muted-foreground/50">·</span>
                  <span>
                    {model.source === "import" ? "Imported" : "Added by hand"}
                  </span>
                </div>
              </div>

              <Button
                variant="ghost"
                size="sm"
                className="text-destructive hover:text-destructive"
                onClick={() => setDeleting(model)}
              >
                <Trash2 data-icon="inline-start" />
                Delete
              </Button>
            </li>
          ))}
        </ul>
      )}

      {importing ? (
        <ImportModelsDialog
          credentials={credentials}
          onOpenChange={setImporting}
          onImported={onChanged}
        />
      ) : null}

      {adding ? (
        <AddModelDialog onOpenChange={setAdding} onCreated={onChanged} />
      ) : null}

      {deleting !== null ? (
        <DeleteConfirmDialog
          onOpenChange={() => setDeleting(null)}
          title={`Delete ${deleting.modelId}?`}
          description="The catalog row goes away, and any role pointing at it falls back to auto. Re-importing brings it back."
          confirmLabel="Delete model"
          pending={deletion.pending}
          error={deletion.error}
          onConfirm={() => void confirmDelete()}
        />
      ) : null}
    </section>
  )
}
