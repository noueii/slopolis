/**
 * Single deterministic dataset shared by every mock handler, so the Sessions
 * list and the Dashboard describe the same sessions.
 */

import { createDataset } from "./data"

export const dataset = createDataset()

export const MODEL_POOL = [
  { id: "gpt-4o", provider: "OpenAI" },
  { id: "gpt-4o-mini", provider: "OpenAI" },
  { id: "claude-sonnet-4", provider: "Anthropic" },
  { id: "claude-opus-4", provider: "Anthropic" },
  { id: "gemini-2.5-pro", provider: "Google" },
] as const

export const MODEL_CATALOG = {
  defaultModelId: "claude-sonnet-4",
  defaultProvider: "Anthropic",
  models: MODEL_POOL.map((model) => ({ id: model.id, provider: model.provider })),
} as const
