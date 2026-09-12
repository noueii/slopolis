import type { XYPosition } from "@xyflow/react"

import type {
  HarnessEdge,
  HarnessNode,
  HarnessNodeKind,
  HarnessRule,
} from "@/api/contract"

const ORCHESTRATOR_X = 40
const AGENT_X = 360
const ORCHESTRATOR_GAP = 180
const AGENT_GAP = 150
const COLUMN_TOP = 40

function columnPosition(index: number, x: number, gap: number): XYPosition {
  return { x, y: COLUMN_TOP + index * gap }
}

export function layoutNodes(
  nodes: HarnessNode[],
): Record<string, XYPosition> {
  const positions: Record<string, XYPosition> = {}
  let orchestratorIndex = 0
  let agentIndex = 0

  for (const node of nodes) {
    if (node.kind === "orchestrator") {
      positions[node.id] = columnPosition(
        orchestratorIndex,
        ORCHESTRATOR_X,
        ORCHESTRATOR_GAP,
      )
      orchestratorIndex += 1
    } else {
      positions[node.id] = columnPosition(agentIndex, AGENT_X, AGENT_GAP)
      agentIndex += 1
    }
  }

  return positions
}

let sequence = 0

function nextId(prefix: string): string {
  sequence += 1
  return `${prefix}_${Date.now().toString(36)}${sequence.toString(36)}`
}

export function newNodeId(): string {
  return nextId("node")
}

export function newEdgeId(): string {
  return nextId("edge")
}

export function newRuleId(): string {
  return nextId("rule")
}

export function createNode(kind: HarnessNodeKind): HarnessNode {
  return {
    id: newNodeId(),
    kind,
    name: kind === "orchestrator" ? "New orchestrator" : "New agent",
    instruction: "",
  }
}

export function createEdge(from: string, to: string): HarnessEdge {
  return { id: newEdgeId(), from, to }
}

export function createRule(): HarnessRule {
  return { id: newRuleId(), title: "New rule", instruction: "" }
}

export function nodeLabel(
  nodes: HarnessNode[],
  nodeId: string | undefined,
): string {
  if (!nodeId) return "Unassigned"
  return nodes.find((node) => node.id === nodeId)?.name ?? "Unassigned"
}
