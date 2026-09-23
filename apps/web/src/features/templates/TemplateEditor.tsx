import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { ArrowLeft, CircleAlert, Loader2 } from "lucide-react"
import type { XYPosition } from "@xyflow/react"

import { api } from "@/api/client"
import type {
  HarnessEdge,
  HarnessNode,
  HarnessNodeKind,
  HarnessRule,
  ReviewTemplateInput,
} from "@/api/contract"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { useModels } from "@/features/providers/lib/useModels"
import { HarnessGraph } from "./HarnessGraph"
import { NodeInspector } from "./NodeInspector"
import { RulesPanel } from "./RulesPanel"
import { createEdge, createNode, layoutNodes, newRuleId } from "./lib/graph"
import { useTemplate } from "./lib/useTemplates"

type SaveState = "idle" | "saving" | "saved" | "error"

export interface TemplateEditorProps {
  templateId: string
  onBack: () => void
}

function toDraft(template: {
  name: string
  description: string
  nodes: HarnessNode[]
  edges: HarnessEdge[]
  rules: HarnessRule[]
}): ReviewTemplateInput {
  return {
    name: template.name,
    description: template.description,
    nodes: template.nodes,
    edges: template.edges,
    rules: template.rules,
  }
}

export function TemplateEditor({ templateId, onBack }: TemplateEditorProps) {
  const { data: template, status, error, refetch } = useTemplate(templateId)
  const models = useModels()

  const [draft, setDraft] = useState<ReviewTemplateInput | null>(null)
  const [positions, setPositions] = useState<Record<string, XYPosition>>({})
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null)
  const [saveState, setSaveState] = useState<SaveState>("idle")
  const hydratedRef = useRef<string | null>(null)
  const firstDraftRef = useRef(true)

  useEffect(() => {
    if (
      template &&
      template.id === templateId &&
      hydratedRef.current !== templateId
    ) {
      hydratedRef.current = templateId
      setDraft(toDraft(template))
      setPositions(layoutNodes(template.nodes))
      setSelectedNodeId(null)
      setSaveState("idle")
    }
  }, [template, templateId])

  const nodes = useMemo(() => draft?.nodes ?? [], [draft])
  const edges = useMemo(() => draft?.edges ?? [], [draft])
  const rules = useMemo(() => draft?.rules ?? [], [draft])

  useEffect(() => {
    if (!draft) return
    setPositions((prev) => {
      const base = layoutNodes(nodes)
      const next: Record<string, XYPosition> = {}
      for (const node of nodes) {
        next[node.id] = prev[node.id] ?? base[node.id] ?? { x: 0, y: 0 }
      }
      return next
    })
    // Re-run only when the node set changes; drag positions live in `prev`.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodes])

  const save = useCallback(async () => {
    if (!draft) return
    setSaveState("saving")
    try {
      await api.updateTemplate(templateId, draft)
      setSaveState("saved")
    } catch {
      setSaveState("error")
    }
  }, [draft, templateId])

  useEffect(() => {
    if (!draft) return
    if (firstDraftRef.current) {
      firstDraftRef.current = false
      return
    }
    const timer = window.setTimeout(() => {
      void save()
    }, 700)
    return () => window.clearTimeout(timer)
  }, [draft, save])

  const handleBack = async () => {
    await save()
    onBack()
  }

  const handleAddNode = useCallback((kind: HarnessNodeKind) => {
    const node = createNode(kind)
    setDraft((prev) =>
      prev ? { ...prev, nodes: [...prev.nodes, node] } : prev,
    )
    setSelectedNodeId(node.id)
  }, [])

  const handleDeleteNode = useCallback((nodeId: string) => {
    setDraft((prev) =>
      prev
        ? {
            ...prev,
            nodes: prev.nodes.filter((node) => node.id !== nodeId),
            edges: prev.edges.filter(
              (edge) => edge.from !== nodeId && edge.to !== nodeId,
            ),
            rules: prev.rules.map((rule) =>
              rule.nodeId === nodeId ? { ...rule, nodeId: undefined } : rule,
            ),
          }
        : prev,
    )
    setSelectedNodeId((prev) => (prev === nodeId ? null : prev))
    setPositions((prev) => {
      const next = { ...prev }
      delete next[nodeId]
      return next
    })
  }, [])

  const handleConnect = useCallback((from: string, to: string) => {
    setDraft((prev) => {
      if (!prev || from === to) return prev
      if (prev.edges.some((edge) => edge.from === from && edge.to === to)) {
        return prev
      }
      return { ...prev, edges: [...prev.edges, createEdge(from, to)] }
    })
  }, [])

  const handleDeleteEdge = useCallback((edgeId: string) => {
    setDraft((prev) =>
      prev
        ? { ...prev, edges: prev.edges.filter((edge) => edge.id !== edgeId) }
        : prev,
    )
  }, [])

  const handleMoveNode = useCallback((nodeId: string, position: XYPosition) => {
    setPositions((prev) => ({ ...prev, [nodeId]: position }))
  }, [])

  const handleUpdateNode = useCallback(
    (nodeId: string, patch: Partial<HarnessNode>) => {
      setDraft((prev) =>
        prev
          ? {
              ...prev,
              nodes: prev.nodes.map((node) =>
                node.id === nodeId ? { ...node, ...patch } : node,
              ),
            }
          : prev,
      )
    },
    [],
  )

  const handleAddRule = useCallback((rule: Omit<HarnessRule, "id">) => {
    setDraft((prev) =>
      prev
        ? { ...prev, rules: [...prev.rules, { ...rule, id: newRuleId() }] }
        : prev,
    )
  }, [])

  const handleUpdateRule = useCallback(
    (ruleId: string, patch: Partial<HarnessRule>) => {
      setDraft((prev) =>
        prev
          ? {
              ...prev,
              rules: prev.rules.map((rule) =>
                rule.id === ruleId ? { ...rule, ...patch } : rule,
              ),
            }
          : prev,
      )
    },
    [],
  )

  const handleRemoveRule = useCallback((ruleId: string) => {
    setDraft((prev) =>
      prev
        ? { ...prev, rules: prev.rules.filter((rule) => rule.id !== ruleId) }
        : prev,
    )
  }, [])

  const ruleCounts = useMemo(() => {
    const counts: Record<string, number> = {}
    for (const rule of rules) {
      if (rule.nodeId) counts[rule.nodeId] = (counts[rule.nodeId] ?? 0) + 1
    }
    return counts
  }, [rules])

  const selectedNode = useMemo(
    () => nodes.find((node) => node.id === selectedNodeId) ?? null,
    [nodes, selectedNodeId],
  )

  if (!draft) {
    return (
      <div className="mx-auto flex w-full max-w-[1500px] animate-fade-up flex-col items-center gap-3 p-6 py-24">
        {status === "error" ? (
          <>
            <span className="grid size-10 place-items-center rounded-full border border-destructive/30 bg-destructive/10 text-destructive">
              <CircleAlert className="size-4" />
            </span>
            <p className="text-sm text-muted-foreground">
              {error ?? "Could not load this template."}
            </p>
            <Button variant="outline" size="sm" onClick={refetch}>
              Retry
            </Button>
          </>
        ) : (
          <>
            <Loader2 className="size-5 animate-spin text-muted-foreground" />
            <p className="text-sm text-muted-foreground">Loading template…</p>
          </>
        )}
      </div>
    )
  }

  return (
    <div className="mx-auto flex w-full max-w-[1500px] animate-fade-up flex-col gap-4 p-6">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <Button variant="outline" size="sm" onClick={() => void handleBack()}>
            <ArrowLeft data-icon="inline-start" />
            Templates
          </Button>
          <div className="flex flex-col">
            <span className="text-sm font-semibold tracking-tight">
              {draft.name || "Untitled template"}
            </span>
            <span className="text-xs text-muted-foreground">
              {nodes.length} {nodes.length === 1 ? "node" : "nodes"} ·{" "}
              {rules.length} {rules.length === 1 ? "rule" : "rules"}
            </span>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <SaveIndicator state={saveState} />
          <Button
            size="sm"
            onClick={() => void save()}
            disabled={saveState === "saving"}
          >
            Save
          </Button>
        </div>
      </header>

      <div className="grid gap-3 sm:grid-cols-2">
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="template-name">Name</Label>
          <Input
            id="template-name"
            value={draft.name}
            onChange={(event) =>
              setDraft((prev) =>
                prev ? { ...prev, name: event.target.value } : prev,
              )
            }
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="template-description">Description</Label>
          <Input
            id="template-description"
            value={draft.description}
            onChange={(event) =>
              setDraft((prev) =>
                prev ? { ...prev, description: event.target.value } : prev,
              )
            }
          />
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_360px]">
        <div className="h-[420px] overflow-hidden rounded-xl border border-border bg-card lg:h-[640px]">
          <HarnessGraph
            nodes={nodes}
            edges={edges}
            positions={positions}
            selectedNodeId={selectedNodeId}
            ruleCounts={ruleCounts}
            onSelectNode={setSelectedNodeId}
            onConnectNodes={handleConnect}
            onDeleteNode={handleDeleteNode}
            onDeleteEdge={handleDeleteEdge}
            onMoveNode={handleMoveNode}
            onAddNode={handleAddNode}
          />
        </div>

        <div className="flex flex-col gap-4 lg:max-h-[640px] lg:overflow-y-auto lg:pr-1 scrollbar-thin">
          <section className="rounded-xl border border-border bg-card p-4">
            <NodeInspector
              node={selectedNode}
              models={models.data?.models ?? []}
              ruleCount={
                selectedNodeId ? (ruleCounts[selectedNodeId] ?? 0) : 0
              }
              onUpdate={(patch) => {
                if (selectedNodeId) handleUpdateNode(selectedNodeId, patch)
              }}
              onDelete={() => {
                if (selectedNodeId) handleDeleteNode(selectedNodeId)
              }}
            />
          </section>

          <section className="rounded-xl border border-border bg-card p-4">
            <RulesPanel
              rules={rules}
              nodes={nodes}
              onAdd={handleAddRule}
              onUpdate={handleUpdateRule}
              onRemove={handleRemoveRule}
            />
          </section>
        </div>
      </div>
    </div>
  )
}

function SaveIndicator({ state }: { state: SaveState }) {
  if (state === "saving") {
    return (
      <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
        <Loader2 className="size-3.5 animate-spin" />
        Saving…
      </span>
    )
  }
  if (state === "saved") {
    return <span className="text-xs text-success">Saved</span>
  }
  if (state === "error") {
    return (
      <span className="flex items-center gap-1.5 text-xs text-destructive">
        <CircleAlert className="size-3.5" />
        Save failed
      </span>
    )
  }
  return null
}
