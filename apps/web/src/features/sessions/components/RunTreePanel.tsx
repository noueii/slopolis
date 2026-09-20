import { useState } from "react"

import type { AgentEventItem, AgentRunNode } from "@/api/contract"

import { findRun } from "../lib/runTree"
import type { RunTreeStatus } from "../lib/useRunTree"
import { RunNodeDetail } from "./RunNodeDetail"
import { RunTreePane } from "./RunTreePane"

export interface RunTreePanelProps {
  sessionId: string
  runs: AgentRunNode[]
  status: RunTreeStatus
  error: string | null
  /** Live stream buffer; the detail pane folds in the selected run's events. */
  liveEvents: AgentEventItem[]
  onRetry: () => void
}

/** The run tree and the selected node's event stream, side by side (spec §7 §UI). */
export function RunTreePanel({
  sessionId,
  runs,
  status,
  error,
  liveEvents,
  onRetry,
}: RunTreePanelProps) {
  const [pickedRunId, setPickedRunId] = useState<string | null>(null)
  // The session's main run is the useful default; an explicit pick wins.
  const activeRunId = pickedRunId ?? runs[0]?.id ?? null
  const activeRun = activeRunId ? findRun(runs, activeRunId) : null

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)] lg:items-start">
      <RunTreePane
        runs={runs}
        status={status}
        error={error}
        selectedRunId={activeRunId}
        onSelect={setPickedRunId}
        onRetry={onRetry}
      />
      <RunNodeDetail
        sessionId={sessionId}
        run={activeRun}
        liveEvents={liveEvents}
      />
    </div>
  )
}
