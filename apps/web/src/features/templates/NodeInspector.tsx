import { Bot, Crown, Trash2 } from "lucide-react"

import type {
  HarnessNode,
  HarnessNodeKind,
  ModelOption,
} from "@/api/contract"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Separator } from "@/components/ui/separator"
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Textarea } from "@/components/ui/textarea"

const AUTO_MODEL = "auto"

export interface NodeInspectorProps {
  node: HarnessNode | null
  models: ModelOption[]
  ruleCount: number
  onUpdate: (patch: Partial<HarnessNode>) => void
  onDelete: () => void
}

export function NodeInspector({
  node,
  models,
  ruleCount,
  onUpdate,
  onDelete,
}: NodeInspectorProps) {
  if (!node) {
    return (
      <div className="flex flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-border px-4 py-10 text-center">
        <span className="grid size-9 place-items-center rounded-full border border-border bg-muted text-muted-foreground">
          <Bot className="size-4" />
        </span>
        <p className="text-[13px] font-medium text-foreground">
          No node selected
        </p>
        <p className="max-w-[220px] text-xs text-muted-foreground">
          Select a node on the canvas to edit its role, model, and instruction.
        </p>
      </div>
    )
  }

  const isOrchestrator = node.kind === "orchestrator"

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <span
            className={
              isOrchestrator
                ? "grid size-7 place-items-center rounded-md bg-accent/10 text-accent"
                : "grid size-7 place-items-center rounded-md bg-muted text-muted-foreground"
            }
          >
            {isOrchestrator ? (
              <Crown className="size-3.5" />
            ) : (
              <Bot className="size-3.5" />
            )}
          </span>
          <span className="text-sm font-semibold tracking-tight">
            {isOrchestrator ? "Orchestrator" : "Sub-agent"}
          </span>
        </div>
        <Button
          variant="ghost"
          size="sm"
          className="text-muted-foreground hover:text-destructive"
          onClick={onDelete}
        >
          <Trash2 data-icon="inline-start" />
          Delete
        </Button>
      </div>

      <Separator />

      <div className="flex flex-col gap-1.5">
        <Label htmlFor="node-name">Name</Label>
        <Input
          id="node-name"
          value={node.name}
          onChange={(event) => onUpdate({ name: event.target.value })}
        />
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="node-kind">Kind</Label>
          <Select
            value={node.kind}
            onValueChange={(value) =>
              onUpdate({ kind: value as HarnessNodeKind })
            }
          >
            <SelectTrigger id="node-kind">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectGroup>
                <SelectItem value="orchestrator">Orchestrator</SelectItem>
                <SelectItem value="agent">Agent</SelectItem>
              </SelectGroup>
            </SelectContent>
          </Select>
        </div>

        <div className="flex flex-col gap-1.5">
          <Label htmlFor="node-model">Model</Label>
          <Select
            value={node.modelId ?? AUTO_MODEL}
            onValueChange={(value) =>
              onUpdate({ modelId: value === AUTO_MODEL ? undefined : value })
            }
          >
            <SelectTrigger id="node-model">
              <SelectValue placeholder="Auto" />
            </SelectTrigger>
            <SelectContent>
              <SelectGroup>
                <SelectItem value={AUTO_MODEL}>Auto (workspace default)</SelectItem>
                {models.map((model) => (
                  <SelectItem key={model.id} value={model.id}>
                    {model.id} · {model.provider}
                  </SelectItem>
                ))}
              </SelectGroup>
            </SelectContent>
          </Select>
        </div>
      </div>

      <div className="flex flex-col gap-1.5">
        <Label htmlFor="node-role">Role</Label>
        <Input
          id="node-role"
          value={node.role ?? ""}
          placeholder="e.g. Session and token handling"
          onChange={(event) => onUpdate({ role: event.target.value })}
        />
      </div>

      <div className="flex flex-col gap-1.5">
        <Label htmlFor="node-instruction">Instruction</Label>
        <Textarea
          id="node-instruction"
          rows={5}
          className="resize-none"
          value={node.instruction ?? ""}
          placeholder="What should this node focus on?"
          onChange={(event) => onUpdate({ instruction: event.target.value })}
        />
      </div>

      <p className="text-xs text-muted-foreground">
        {ruleCount} {ruleCount === 1 ? "rule" : "rules"} assigned to this node
      </p>
    </div>
  )
}
