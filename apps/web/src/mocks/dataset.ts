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

export const REVIEW_PRESET_CATALOG = {
  defaultPresetId: "default",
  presets: [
    { id: "default", name: "Default", description: "Balanced built-in review across every target." },
    { id: "security", name: "Security audit", description: "Prioritizes auth, injection, and secret-handling risks." },
    { id: "performance", name: "Performance review", description: "Focuses on hot paths, N+1s, and allocation pressure." },
    { id: "tests", name: "Test coverage", description: "Flags untested branches and missing edge cases." },
  ],
} as const
