import { useState } from "react"
import { Pencil, Plus, Trash2 } from "lucide-react"

import type { HarnessNode, HarnessRule } from "@/api/contract"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Textarea } from "@/components/ui/textarea"

const UNASSIGNED = "unassigned"

interface RuleFormState {
  title: string
  instruction: string
  nodeId: string
}

export interface RulesPanelProps {
  rules: HarnessRule[]
  nodes: HarnessNode[]
  onAdd: (rule: Omit<HarnessRule, "id">) => void
  onUpdate: (id: string, patch: Partial<HarnessRule>) => void
  onRemove: (id: string) => void
}

export function RulesPanel({
  rules,
  nodes,
  onAdd,
  onUpdate,
  onRemove,
}: RulesPanelProps) {
  const [formOpen, setFormOpen] = useState(false)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [form, setForm] = useState<RuleFormState>({
    title: "",
    instruction: "",
    nodeId: UNASSIGNED,
  })

  const openAdd = () => {
    setEditingId(null)
    setForm({ title: "", instruction: "", nodeId: UNASSIGNED })
    setFormOpen(true)
  }

  const openEdit = (rule: HarnessRule) => {
    setEditingId(rule.id)
    setForm({
      title: rule.title,
      instruction: rule.instruction,
      nodeId: rule.nodeId ?? UNASSIGNED,
    })
    setFormOpen(true)
  }

  const closeForm = () => {
    setFormOpen(false)
    setEditingId(null)
  }

  const submitForm = () => {
    const title = form.title.trim()
    if (!title) return
    const nodeId = form.nodeId === UNASSIGNED ? undefined : form.nodeId
    const payload = { title, instruction: form.instruction.trim(), nodeId }
    if (editingId) onUpdate(editingId, payload)
    else onAdd(payload)
    closeForm()
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className="text-sm font-semibold tracking-tight">Rules</span>
          <span className="rounded-full border border-border bg-muted/60 px-1.5 py-px font-mono text-[10px] tabular text-muted-foreground">
            {rules.length}
          </span>
        </div>
        <Button size="sm" variant="outline" onClick={openAdd}>
          <Plus data-icon="inline-start" />
          Add rule
        </Button>
      </div>

      {formOpen ? (
        <div className="flex flex-col gap-3 rounded-lg border border-border bg-muted/30 p-3">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="rule-title">Title</Label>
            <Input
              id="rule-title"
              value={form.title}
              placeholder="e.g. Every finding cites file and line"
              onChange={(event) =>
                setForm((prev) => ({ ...prev, title: event.target.value }))
              }
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="rule-instruction">Instruction</Label>
            <Textarea
              id="rule-instruction"
              rows={3}
              className="resize-none"
              value={form.instruction}
              placeholder="What must the harness enforce?"
              onChange={(event) =>
                setForm((prev) => ({ ...prev, instruction: event.target.value }))
              }
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="rule-node">Applies to</Label>
            <Select
              value={form.nodeId}
              onValueChange={(value) =>
                setForm((prev) => ({ ...prev, nodeId: value }))
              }
            >
              <SelectTrigger id="rule-node">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectGroup>
                  <SelectItem value={UNASSIGNED}>
                    Unassigned (whole harness)
                  </SelectItem>
                  {nodes.map((node) => (
                    <SelectItem key={node.id} value={node.id}>
                      {node.name}
                    </SelectItem>
                  ))}
                </SelectGroup>
              </SelectContent>
            </Select>
          </div>
          <div className="flex items-center justify-end gap-2">
            <Button size="sm" variant="ghost" onClick={closeForm}>
              Cancel
            </Button>
            <Button
              size="sm"
              onClick={submitForm}
              disabled={form.title.trim().length === 0}
            >
              {editingId ? "Save rule" : "Add rule"}
            </Button>
          </div>
        </div>
      ) : null}

      {rules.length === 0 && !formOpen ? (
        <p className="rounded-lg border border-dashed border-border px-3 py-6 text-center text-xs text-muted-foreground">
          No rules yet. Add one to constrain how the harness reviews.
        </p>
      ) : null}

      <div className="flex flex-col gap-2">
        {rules.map((rule) => {
          const assigned = rule.nodeId !== undefined
          return (
            <div
              key={rule.id}
              className="flex flex-col gap-2 rounded-lg border border-border bg-card px-3 py-2.5"
            >
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-1.5">
                    <p className="truncate text-[13px] font-medium text-foreground">
                      {rule.title}
                    </p>
                    {!assigned ? (
                      <span className="shrink-0 rounded border border-warning/30 bg-warning/10 px-1.5 py-px font-mono text-[10px] uppercase tracking-wide text-warning">
                        Unassigned
                      </span>
                    ) : null}
                  </div>
                  {rule.instruction ? (
                    <p className="mt-0.5 line-clamp-2 text-xs text-muted-foreground">
                      {rule.instruction}
                    </p>
                  ) : (
                    <p className="mt-0.5 text-xs italic text-muted-foreground">
                      No instruction
                    </p>
                  )}
                </div>
                <div className="flex shrink-0 items-center gap-0.5">
                  <Button
                    size="icon"
                    variant="ghost"
                    className="size-7 text-muted-foreground"
                    aria-label={`Edit ${rule.title}`}
                    onClick={() => openEdit(rule)}
                  >
                    <Pencil className="size-3.5" />
                  </Button>
                  <Button
                    size="icon"
                    variant="ghost"
                    className="size-7 text-muted-foreground hover:text-destructive"
                    aria-label={`Remove ${rule.title}`}
                    onClick={() => onRemove(rule.id)}
                  >
                    <Trash2 className="size-3.5" />
                  </Button>
                </div>
              </div>

              <div className="flex items-center gap-2">
                <span className="shrink-0 text-2xs uppercase tracking-widest text-muted-foreground">
                  Applies to
                </span>
                <Select
                  value={rule.nodeId ?? UNASSIGNED}
                  onValueChange={(value) =>
                    onUpdate(rule.id, {
                      nodeId: value === UNASSIGNED ? undefined : value,
                    })
                  }
                >
                  <SelectTrigger
                    className="h-7 w-full text-xs"
                    aria-label={`Assign ${rule.title} to a node`}
                  >
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectGroup>
                      <SelectItem value={UNASSIGNED}>Unassigned</SelectItem>
                      {nodes.map((node) => (
                        <SelectItem key={node.id} value={node.id}>
                          {node.name}
                        </SelectItem>
                      ))}
                    </SelectGroup>
                  </SelectContent>
                </Select>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
