import { useCallback, useMemo } from "react"
import { Bot, Crown, Trash2 } from "lucide-react"
import {
  Background,
  Controls,
  Handle,
  MarkerType,
  MiniMap,
  Position,
  ReactFlow,
  type Connection,
  type Edge,
  type EdgeChange,
  type Node,
  type NodeChange,
  type NodeProps,
  type NodeTypes,
  type XYPosition,
} from "@xyflow/react"

import { cn } from "@/lib/utils"
import type {
  HarnessEdge,
  HarnessNode,
  HarnessNodeKind,
} from "@/api/contract"
import { Button } from "@/components/ui/button"

interface HarnessFlowData extends Record<string, unknown> {
  node: HarnessNode
  ruleCount: number
  onDelete: (id: string) => void
}

type HarnessFlowNode = Node<HarnessFlowData, "harness">

function HarnessNodeCard({ data, selected }: NodeProps<HarnessFlowNode>) {
  const { node, ruleCount, onDelete } = data
  const isOrchestrator = node.kind === "orchestrator"

  return (
    <div
      className={cn(
        "group relative w-[224px] rounded-xl border bg-card px-3 py-2.5 text-left shadow-sm transition-shadow",
        isOrchestrator ? "border-accent/50" : "border-border",
        selected && "ring-2 ring-ring ring-offset-2 ring-offset-background",
      )}
    >
      <Handle
        type="target"
        position={Position.Left}
        className="!size-2 !border !border-border !bg-background"
      />
      <Handle
        type="source"
        position={Position.Right}
        className="!size-2 !border !border-border !bg-background"
      />

      <button
        type="button"
        aria-label={`Delete ${node.name}`}
        onClick={(event) => {
          event.stopPropagation()
          onDelete(node.id)
        }}
        className="nodrag nopan absolute -right-2 -top-2 hidden size-5 place-items-center rounded-full border border-border bg-card text-muted-foreground shadow-sm transition-colors hover:border-destructive hover:text-destructive group-hover:grid"
      >
        <Trash2 className="size-3" />
      </button>

      <div className="flex items-start gap-2.5">
        <span
          className={cn(
            "grid size-7 shrink-0 place-items-center rounded-md",
            isOrchestrator
              ? "bg-accent/10 text-accent"
              : "bg-muted text-muted-foreground",
          )}
        >
          {isOrchestrator ? (
            <Crown className="size-3.5" />
          ) : (
            <Bot className="size-3.5" />
          )}
        </span>
        <div className="flex min-w-0 flex-1 flex-col gap-0.5">
          <span className="truncate text-[13px] font-semibold text-foreground">
            {node.name}
          </span>
          <span className="truncate text-[11px] text-muted-foreground">
            {node.role || (isOrchestrator ? "Orchestrator" : "Agent")}
          </span>
          <div className="mt-1 flex flex-wrap items-center gap-1">
            <span
              className={cn(
                "rounded border px-1.5 py-px font-mono text-[10px] uppercase tracking-wide",
                isOrchestrator
                  ? "border-accent/30 bg-accent/10 text-accent"
                  : "border-border bg-muted/60 text-muted-foreground",
              )}
            >
              {isOrchestrator ? "Orchestrator" : "Agent"}
            </span>
            {node.modelId ? (
              <span className="rounded border border-border bg-background px-1.5 py-px font-mono text-[10px] text-info">
                {node.modelId}
              </span>
            ) : null}
            {ruleCount > 0 ? (
              <span className="rounded border border-border bg-background px-1.5 py-px font-mono text-[10px] text-muted-foreground">
                {ruleCount} {ruleCount === 1 ? "rule" : "rules"}
              </span>
            ) : null}
          </div>
        </div>
      </div>
    </div>
  )
}

export interface HarnessGraphProps {
  nodes: HarnessNode[]
  edges: HarnessEdge[]
  positions: Record<string, XYPosition>
  selectedNodeId: string | null
  ruleCounts: Record<string, number>
  onSelectNode: (id: string | null) => void
  onConnectNodes: (from: string, to: string) => void
  onDeleteNode: (id: string) => void
  onDeleteEdge: (id: string) => void
  onMoveNode: (id: string, position: XYPosition) => void
  onAddNode: (kind: HarnessNodeKind) => void
}

export function HarnessGraph({
  nodes,
  edges,
  positions,
  selectedNodeId,
  ruleCounts,
  onSelectNode,
  onConnectNodes,
  onDeleteNode,
  onDeleteEdge,
  onMoveNode,
  onAddNode,
}: HarnessGraphProps) {
  const flowNodes = useMemo<HarnessFlowNode[]>(
    () =>
      nodes.map((node) => ({
        id: node.id,
        type: "harness" as const,
        position: positions[node.id] ?? { x: 0, y: 0 },
        selected: node.id === selectedNodeId,
        data: {
          node,
          ruleCount: ruleCounts[node.id] ?? 0,
          onDelete: onDeleteNode,
        },
      })),
    [nodes, positions, selectedNodeId, ruleCounts, onDeleteNode],
  )

  const flowEdges = useMemo<Edge[]>(
    () =>
      edges.map((edge) => ({
        id: edge.id,
        source: edge.from,
        target: edge.to,
        type: "smoothstep",
        markerEnd: {
          type: MarkerType.ArrowClosed,
          width: 18,
          height: 18,
          color: "hsl(var(--muted-foreground))",
        },
        style: { stroke: "hsl(var(--border))", strokeWidth: 1.5 },
      })),
    [edges],
  )

  const handleNodesChange = useCallback(
    (changes: NodeChange<HarnessFlowNode>[]) => {
      for (const change of changes) {
        if (change.type === "position" && change.position) {
          onMoveNode(change.id, change.position)
        } else if (change.type === "remove") {
          onDeleteNode(change.id)
        } else if (change.type === "select") {
          if (change.selected) onSelectNode(change.id)
          else if (change.id === selectedNodeId) onSelectNode(null)
        }
      }
    },
    [onMoveNode, onDeleteNode, onSelectNode, selectedNodeId],
  )

  const handleEdgesChange = useCallback(
    (changes: EdgeChange[]) => {
      for (const change of changes) {
        if (change.type === "remove") onDeleteEdge(change.id)
      }
    },
    [onDeleteEdge],
  )

  const handleConnect = useCallback(
    (connection: Connection) => {
      if (!connection.source || !connection.target) return
      if (connection.source === connection.target) return
      onConnectNodes(connection.source, connection.target)
    },
    [onConnectNodes],
  )

  const nodeTypes = useMemo<NodeTypes>(
    () => ({ harness: HarnessNodeCard }),
    [],
  )

  return (
    <div className="relative h-full w-full">
      <div className="pointer-events-none absolute left-3 top-3 z-10 flex items-center gap-1.5">
        <Button
          size="sm"
          variant="secondary"
          className="pointer-events-auto shadow-sm"
          onClick={() => onAddNode("orchestrator")}
        >
          <Crown data-icon="inline-start" />
          Orchestrator
        </Button>
        <Button
          size="sm"
          variant="secondary"
          className="pointer-events-auto shadow-sm"
          onClick={() => onAddNode("agent")}
        >
          <Bot data-icon="inline-start" />
          Agent
        </Button>
      </div>

      <ReactFlow
        nodes={flowNodes}
        edges={flowEdges}
        nodeTypes={nodeTypes}
        onNodesChange={handleNodesChange}
        onEdgesChange={handleEdgesChange}
        onConnect={handleConnect}
        onNodeClick={(_, node) => onSelectNode(node.id)}
        onPaneClick={() => onSelectNode(null)}
        nodesDraggable
        fitView
        fitViewOptions={{ padding: 0.35 }}
        className="bg-muted/20"
      >
        <Background gap={18} size={1} color="hsl(var(--border))" />
        <Controls
          className="!border-border !bg-card !shadow-sm"
          showInteractive={false}
        />
        <MiniMap
          pannable
          zoomable
          className="!border-border !bg-card"
          maskColor="hsl(var(--background) / 0.6)"
          nodeColor={(node) =>
            (node.data as HarnessFlowData).node.kind === "orchestrator"
              ? "hsl(var(--accent))"
              : "hsl(var(--muted-foreground))"
          }
        />
      </ReactFlow>
    </div>
  )
}
