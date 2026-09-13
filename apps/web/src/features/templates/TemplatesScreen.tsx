import { useState } from "react"
import {
  ArrowUpRight,
  CircleAlert,
  Loader2,
  Plus,
  RefreshCw,
  Workflow,
} from "lucide-react"

import { api } from "@/api/client"
import type { ReviewTemplate } from "@/api/contract"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { formatRelativeTime } from "@/features/sessions/lib/format"
import { TemplateEditor } from "./TemplateEditor"
import { createNode } from "./lib/graph"
import { useTemplates } from "./lib/useTemplates"

export function TemplatesScreen() {
  const { data, status, error, refetch } = useTemplates()
  const [editingId, setEditingId] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)
  const [createError, setCreateError] = useState<string | null>(null)

  const handleNew = async () => {
    setCreating(true)
    setCreateError(null)
    try {
      const created = await api.createTemplate({
        name: "Untitled template",
        description: "",
        nodes: [createNode("orchestrator")],
        edges: [],
        rules: [],
      })
      setEditingId(created.id)
    } catch {
      setCreateError("Could not create a template. Try again.")
    } finally {
      setCreating(false)
    }
  }

  if (editingId) {
    return (
      <TemplateEditor
        key={editingId}
        templateId={editingId}
        onBack={() => {
          setEditingId(null)
          refetch()
        }}
      />
    )
  }

  const items = data?.items ?? []
  const showSkeleton = status === "loading" && !data
  const showError = status === "error"
  const showEmpty = !showError && data !== null && items.length === 0
  const showList = !showError && items.length > 0

  return (
    <div className="mx-auto flex w-full max-w-[1500px] animate-fade-up flex-col gap-5 p-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div className="flex flex-col gap-1">
          <h1 className="text-xl font-semibold tracking-tight">
            Review templates
          </h1>
          <p className="max-w-2xl text-sm text-muted-foreground">
            Define review harnesses: an orchestrator, the sub-agents it delegates
            to, and the rules they enforce.
          </p>
        </div>
        <Button size="sm" onClick={() => void handleNew()} disabled={creating}>
          {creating ? (
            <Loader2 className="size-4 animate-spin" />
          ) : (
            <Plus data-icon="inline-start" />
          )}
          {creating ? "Creating…" : "New template"}
        </Button>
      </header>

      {createError ? (
        <p className="flex items-center gap-2 rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive">
          <CircleAlert className="size-3.5 shrink-0" />
          {createError}
        </p>
      ) : null}

      {showSkeleton ? <TemplateGridSkeleton /> : null}

      {showError ? (
        <div className="flex flex-col items-center gap-3 rounded-xl border border-dashed border-border bg-card/50 px-6 py-16 text-center">
          <span className="grid size-10 place-items-center rounded-full border border-destructive/30 bg-destructive/10 text-destructive">
            <CircleAlert className="size-4" />
          </span>
          <p className="text-sm text-muted-foreground">
            {error ?? "Could not load review templates."}
          </p>
          <Button variant="outline" size="sm" onClick={refetch}>
            <RefreshCw data-icon="inline-start" />
            Retry
          </Button>
        </div>
      ) : null}

      {showEmpty ? (
        <div className="flex flex-col items-center gap-3 rounded-xl border border-dashed border-border bg-card/50 px-6 py-16 text-center">
          <span className="grid size-11 place-items-center rounded-full border border-border bg-muted text-muted-foreground">
            <Workflow className="size-5" />
          </span>
          <div className="flex flex-col gap-1">
            <h2 className="text-base font-semibold tracking-tight">
              No review templates yet
            </h2>
            <p className="max-w-md text-sm text-muted-foreground">
              Create your first harness to shape how reviews run: who
              orchestrates, who reviews, and what rules apply.
            </p>
          </div>
          <Button
            size="sm"
            onClick={() => void handleNew()}
            disabled={creating}
          >
            <Plus data-icon="inline-start" />
            New template
          </Button>
        </div>
      ) : null}

      {showList ? (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {items.map((template) => (
            <TemplateCard
              key={template.id}
              template={template}
              onOpen={() => setEditingId(template.id)}
            />
          ))}
        </div>
      ) : null}
    </div>
  )
}

interface TemplateCardProps {
  template: ReviewTemplate
  onOpen: () => void
}

function TemplateCard({ template, onOpen }: TemplateCardProps) {
  const orchestratorCount = template.nodes.filter(
    (node) => node.kind === "orchestrator",
  ).length
  const agentCount = template.nodes.filter(
    (node) => node.kind === "agent",
  ).length

  return (
    <button
      type="button"
      onClick={onOpen}
      aria-label={`Open ${template.name}`}
      className="group flex flex-col gap-4 rounded-xl border border-border bg-card p-5 text-left shadow-sm transition-all hover:border-accent/50 hover:shadow focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
    >
      <div className="flex items-start justify-between gap-3">
        <span className="grid size-9 shrink-0 place-items-center rounded-lg border border-accent/30 bg-accent/10 text-accent">
          <Workflow className="size-4" />
        </span>
        <ArrowUpRight className="size-4 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100" />
      </div>

      <div className="flex flex-col gap-1">
        <h2 className="text-sm font-semibold tracking-tight text-foreground">
          {template.name}
        </h2>
        <p className="line-clamp-2 text-xs text-muted-foreground">
          {template.description || "No description yet."}
        </p>
      </div>

      <div className="mt-auto flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-border pt-3 text-2xs text-muted-foreground">
        <span className="font-mono tabular">
          {orchestratorCount} orch · {agentCount} agents
        </span>
        <span className="font-mono tabular">{template.rules.length} rules</span>
        <span className="ml-auto">
          Updated {formatRelativeTime(template.updatedAt)}
        </span>
      </div>
    </button>
  )
}

function TemplateGridSkeleton() {
  return (
    <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
      {Array.from({ length: 3 }).map((_, index) => (
        <div
          key={index}
          className="flex flex-col gap-4 rounded-xl border border-border bg-card p-5"
        >
          <Skeleton className="size-9 rounded-lg" />
          <div className="flex flex-col gap-2">
            <Skeleton className="h-4 w-32" />
            <Skeleton className="h-3 w-full" />
            <Skeleton className="h-3 w-2/3" />
          </div>
          <Skeleton className="h-3 w-40" />
        </div>
      ))}
    </div>
  )
}
