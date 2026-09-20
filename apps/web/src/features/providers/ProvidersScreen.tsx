import { useCallback, useMemo } from "react"
import { RefreshCw } from "lucide-react"

import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import { ModelCatalogSection } from "./components/ModelCatalogSection"
import { ProviderCredentialsSection } from "./components/ProviderCredentialsSection"
import { ProvidersError, ProvidersSkeleton } from "./components/ProvidersStates"
import { RoleAssignmentsSection } from "./components/RoleAssignmentsSection"
import { useAssignments, useCatalog, useProviders } from "./lib/useProviders"

/**
 * The provider & model admin surface (spec 10.2): the vault's credentials, the
 * workspace model catalog, and the role → model assignments.
 *
 * The three resources load independently but describe one page, so the first
 * refusal takes over the screen — a partially configured view would invite
 * edits against state the server never confirmed.
 */
export function ProvidersScreen() {
  const providers = useProviders()
  const catalog = useCatalog()
  const assignments = useAssignments()
  const { refetch: refetchProviders } = providers
  const { refetch: refetchCatalog } = catalog
  const { refetch: refetchAssignments } = assignments

  const reload = useCallback(() => {
    refetchProviders()
    refetchCatalog()
    refetchAssignments()
  }, [refetchProviders, refetchCatalog, refetchAssignments])

  const busy =
    providers.status === "loading" ||
    catalog.status === "loading" ||
    assignments.status === "loading"

  const failure =
    providers.status === "error"
      ? providers.error
      : catalog.status === "error"
        ? catalog.error
        : assignments.status === "error"
          ? assignments.error
          : null

  const loading =
    failure === null &&
    (providers.data === null ||
      catalog.data === null ||
      assignments.data === null)

  // Memoised per loaded payload so section props keep a stable identity across
  // the re-renders a mutation causes.
  const credentials = useMemo(
    () => providers.data?.items ?? [],
    [providers.data],
  )
  const models = useMemo(() => catalog.data?.items ?? [], [catalog.data])
  const roleRows = useMemo(
    () => assignments.data?.roles ?? [],
    [assignments.data],
  )

  const importedByCredential = useMemo(() => {
    const counts: Record<string, number> = {}
    for (const model of models) {
      if (model.credentialId === null) continue
      counts[model.credentialId] = (counts[model.credentialId] ?? 0) + 1
    }
    return counts
  }, [models])

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
          <h1 className="text-xl font-semibold tracking-tight">
            Providers &amp; models
          </h1>
          <p className="max-w-2xl text-sm text-muted-foreground">
            Bring your own keys, choose which models the workspace can use, and
            say which model each review role runs on.
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={reload}>
          <RefreshCw data-icon="inline-start" className={cn(busy && "animate-spin")} />
          Refresh
        </Button>
      </header>

      {failure !== null ? (
        <ProvidersError message={failure} onRetry={reload} />
      ) : null}

      {loading ? <ProvidersSkeleton /> : null}

      {failure === null && !loading ? (
        <>
          <ProviderCredentialsSection
            credentials={credentials}
            importedByCredential={importedByCredential}
            onChanged={reload}
          />
          <ModelCatalogSection
            models={models}
            defaultModelId={catalog.data?.defaultModelId ?? null}
            credentials={credentials}
            onChanged={reload}
          />
          <RoleAssignmentsSection
            roles={roleRows}
            models={models}
            defaultModelId={assignments.data?.defaultModelId ?? null}
            onChanged={reload}
          />
        </>
      ) : null}
    </div>
  )
}
