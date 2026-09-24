/**
 * Human-readable projection of one persisted harness event (spec v2 §7).
 *
 * The node detail pane is a reading surface, not a debugger: every event is
 * rendered from the typed fields the redaction allow-list keeps, so no raw
 * payload is ever dumped at the reader. An event type this build does not know
 * still renders — as its type plus a short scalar digest — so a newer worker
 * cannot make the pane look broken.
 */

import type {
  AgentEventItem,
  AgentTurnMessage,
  AgentTurnTranscript,
} from "@/api/contract"
import { formatCost, formatTokens } from "./format"

export type RunEventTone = "neutral" | "info" | "success" | "warning" | "danger"

export interface RunEventView {
  /** Short kind label, e.g. `Tool call`. */
  label: string
  /** One line of prose built from allow-listed fields. */
  detail: string
  tone: RunEventTone
  /**
   * Parsed `agent.turn` transcript, when this event carries one. `null` for
   * every other event type, and for a turn payload this build cannot read —
   * the row then falls back to its one-line digest.
   */
  transcript?: AgentTurnTranscript | null
}

const MAX_DIGEST_FIELDS = 3
const MAX_DIGEST_VALUE = 80

function text(payload: Record<string, unknown>, key: string): string | null {
  const value = payload[key]
  return typeof value === "string" && value.length > 0 ? value : null
}

function count(payload: Record<string, unknown>, key: string): number | null {
  const value = payload[key]
  return typeof value === "number" && Number.isFinite(value) ? value : null
}

function titleCase(value: string): string {
  return value.charAt(0).toUpperCase() + value.slice(1)
}

function truncate(value: string): string {
  return value.length > MAX_DIGEST_VALUE
    ? `${value.slice(0, MAX_DIGEST_VALUE - 1)}…`
    : value
}

/** Short `key: value` digest for an event type this build does not know. */
function digest(payload: Record<string, unknown>): string {
  const parts: string[] = []
  for (const [key, value] of Object.entries(payload)) {
    if (parts.length >= MAX_DIGEST_FIELDS) break
    if (value === null || value === undefined) continue
    if (typeof value === "string") {
      if (value.length === 0) continue
      parts.push(`${key}: ${truncate(value)}`)
    } else if (typeof value === "number" || typeof value === "boolean") {
      parts.push(`${key}: ${String(value)}`)
    }
  }
  return parts.length > 0 ? parts.join(" · ") : "No details recorded."
}

/**
 * Read an `agent.turn` payload's transcript defensively. A worker newer or
 * older than this build may shape the payload differently, and a turn that
 * cannot be read must not break the pane: anything unexpected yields `null`,
 * and the row still renders its one-line digest.
 */
function turnTranscript(
  payload: Record<string, unknown>,
): AgentTurnTranscript | null {
  const rawMessages = payload.messages
  if (!Array.isArray(rawMessages)) return null
  const messages: AgentTurnMessage[] = []
  for (const raw of rawMessages) {
    if (typeof raw !== "object" || raw === null) return null
    if (!("role" in raw) || !("content" in raw)) return null
    const { role, content } = raw
    if (typeof role !== "string" || typeof content !== "string") return null
    messages.push({ role, content })
  }
  const response = payload.response
  return {
    messages,
    response: typeof response === "string" ? response : null,
    truncated: payload.truncated === true,
  }
}

/** Describe one event for the node detail pane. */
export function describeRunEvent(event: AgentEventItem): RunEventView {
  const { payload } = event
  switch (event.type) {
    case "agent.spawned": {
      const role = text(payload, "role")
      const level = text(payload, "level")
      const objective = text(payload, "objective")
      const lead = [role, level ? `level ${level}` : null]
        .filter(Boolean)
        .join(" · ")
      return {
        label: "Spawned",
        detail: objective ? `${lead} — ${objective}` : `${lead} was spawned.`,
        tone: "info",
      }
    }
    case "agent.started": {
      const model = text(payload, "model_id")
      return {
        label: "Started",
        detail: model ? `Running on ${model}.` : "Run started.",
        tone: "info",
      }
    }
    case "agent.step": {
      const step = count(payload, "step")
      const summary = text(payload, "summary")
      const calls = count(payload, "tool_call_count")
      const detail =
        summary ??
        (calls !== null
          ? `${calls} tool ${calls === 1 ? "call" : "calls"}.`
          : "Model turn.")
      return {
        label: step === null ? "Step" : `Step ${step}`,
        detail,
        tone: "neutral",
      }
    }
    case "agent.tool_call": {
      const tool = text(payload, "tool") ?? "tool"
      const args = text(payload, "args_summary")
      return {
        label: "Tool call",
        detail: args ? `${tool} — ${args}` : `${tool}`,
        tone: "neutral",
      }
    }
    case "agent.tool_result": {
      const tool = text(payload, "tool") ?? "tool"
      const ok = payload.ok !== false
      const summary = text(payload, "summary")
      const results = count(payload, "result_count")
      const detail =
        summary ??
        (results !== null
          ? `${results} ${results === 1 ? "result" : "results"}`
          : `${tool} returned.`)
      return {
        label: ok ? "Tool result" : "Tool failed",
        detail: `${tool}: ${detail}`,
        tone: ok ? "neutral" : "danger",
      }
    }
    case "agent.message": {
      const summary = text(payload, "summary")
      const chars = count(payload, "chars")
      return {
        label: "Message",
        detail:
          summary ??
          (chars !== null ? `Assistant text (${chars} chars).` : "Assistant text."),
        tone: "neutral",
      }
    }
    case "agent.finding": {
      const severity = text(payload, "severity")
      const category = text(payload, "category")
      const where = [text(payload, "path"), count(payload, "line")]
        .filter((part) => part !== null)
        .join(":")
      const message = text(payload, "message") ?? text(payload, "suggestion")
      const suggestion = text(payload, "suggestion")
      const head = [where, message].filter(Boolean).join(" — ")
      return {
        label: severity ? `Finding · ${titleCase(severity)}` : "Finding",
        detail: [
          category ? `${titleCase(category)}: ${head || "recorded."}` : head || "Recorded.",
          suggestion && suggestion !== message ? `Suggested: ${suggestion}` : null,
        ]
          .filter(Boolean)
          .join(" "),
        tone: severity === "high" || severity === "critical" ? "warning" : "info",
      }
    }
    case "agent.turn": {
      const model = text(payload, "model_id")
      const transcript = turnTranscript(payload)
      const total = count(payload, "total_tokens")
      const cost = count(payload, "cost_usd")
      // The digest stays readable even when the transcript could not be read:
      // it counts the raw array when the shape is unknown, so a newer payload
      // still says something true about the call.
      const messageCount =
        transcript?.messages.length ??
        (Array.isArray(payload.messages) ? payload.messages.length : null)
      const detail = [
        model,
        messageCount === null
          ? null
          : `${messageCount} ${messageCount === 1 ? "message" : "messages"}`,
        total === null ? null : `${formatTokens(total)} tokens`,
        cost === null ? null : formatCost(cost),
      ]
        .filter(Boolean)
        .join(" · ")
      return {
        label: "Model turn",
        detail: detail ? `${detail}.` : "Turn recorded.",
        tone: "info",
        transcript,
      }
    }
    case "agent.completed": {
      const summary = text(payload, "summary") ?? "Run finished."
      const tokens = count(payload, "tokens_used")
      const cost = count(payload, "cost_usd")
      const usage =
        tokens === null
          ? null
          : `${formatTokens(tokens)} tokens · ${formatCost(cost ?? 0)}`
      return {
        label: "Completed",
        detail: usage ? `${summary} ${usage}` : summary,
        tone: "success",
      }
    }
    case "agent.failed":
      return {
        label: "Failed",
        detail: text(payload, "error") ?? text(payload, "summary") ?? "Run failed.",
        tone: "danger",
      }
    case "agent.cancelled":
      return {
        label: "Cancelled",
        detail: text(payload, "summary") ?? "Run cancelled.",
        tone: "warning",
      }
    default:
      return { label: event.type, detail: digest(payload), tone: "neutral" }
  }
}
