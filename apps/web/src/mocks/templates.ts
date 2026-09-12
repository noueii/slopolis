/**
 * MSW handlers for review templates (mock layer only).
 *
 * Serves the shapes in `src/api/contract.ts` from a module-level mutable store
 * so creates and patches survive for the browser session. Empty/error/slow are
 * driven by the `x-mock-scenario` header the API client attaches.
 */

import { HttpResponse, delay, http } from "msw"

import type {
  ApiErrorBody,
  ReviewTemplate,
  ReviewTemplateInput,
  ReviewTemplateListResponse,
} from "@/api/contract"

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "/api"

function scenarioOf(request: Request): string {
  return request.headers.get("x-mock-scenario") ?? "default"
}

async function latency(request: Request): Promise<void> {
  if (scenarioOf(request) === "slow") {
    await delay(2200)
    return
  }
  await delay(180 + Math.floor(Math.random() * 240))
}

function errorResponse(
  status: number,
  code: string,
  message: string,
  detail?: string,
): Response {
  const body: ApiErrorBody = { error: { code, message, detail } }
  return HttpResponse.json(body, { status })
}

function seedTemplates(): ReviewTemplate[] {
  return [
    {
      id: "tpl_security_audit",
      name: "Security deep review",
      description:
        "An orchestrator splits the diff across auth, injection, and dependency specialists, then merges only verified findings.",
      updatedAt: "2026-08-30T14:20:00.000Z",
      nodes: [
        {
          id: "n_orch",
          kind: "orchestrator",
          name: "Lead orchestrator",
          role: "Triages the diff and delegates",
          modelId: "claude-opus-4",
          instruction:
            "Read the full diff, profile risk, and delegate focused slices to sub-agents. Synthesize only verified findings.",
        },
        {
          id: "n_auth",
          kind: "agent",
          name: "Auth specialist",
          role: "Session and token handling",
          modelId: "claude-sonnet-4",
          instruction:
            "Audit authentication, session refresh, and permission checks for bypasses and privilege escalation.",
        },
        {
          id: "n_injection",
          kind: "agent",
          name: "Injection specialist",
          role: "Untrusted input paths",
          modelId: "gpt-4o",
          instruction:
            "Trace untrusted input to sinks. Flag SQL, command, and template injection with a concrete exploit path.",
        },
        {
          id: "n_deps",
          kind: "agent",
          name: "Dependency auditor",
          role: "Supply chain",
          modelId: "gemini-2.5-pro",
          instruction:
            "Check new and bumped dependencies for known-vulnerable versions and unsafe install scripts.",
        },
      ],
      edges: [
        { id: "e_orch_auth", from: "n_orch", to: "n_auth" },
        { id: "e_orch_injection", from: "n_orch", to: "n_injection" },
        { id: "e_orch_deps", from: "n_orch", to: "n_deps" },
      ],
      rules: [
        {
          id: "r_secrets",
          title: "No secret material in logs",
          instruction:
            "Never quote tokens, keys, or credentials in a finding; redact to the first four characters.",
          nodeId: "n_auth",
        },
        {
          id: "r_evidence",
          title: "Every finding cites file and line",
          instruction:
            "A finding without a file path and line range is dropped before synthesis.",
        },
        {
          id: "r_exploit",
          title: "Prove exploitability",
          instruction:
            "Security findings must include a realistic attack path or be downgraded to info.",
          nodeId: "n_injection",
        },
      ],
    },
    {
      id: "tpl_quick_pass",
      name: "Quick correctness pass",
      description:
        "A single sub-agent reviews the diff for logic errors and missing edge cases. Fast and cheap.",
      updatedAt: "2026-08-24T09:05:00.000Z",
      nodes: [
        {
          id: "n_quick_orch",
          kind: "orchestrator",
          name: "Review lead",
          role: "Scopes and reports",
          modelId: "claude-sonnet-4",
          instruction:
            "Scope the diff to the changed behavior and hand it to the correctness agent.",
        },
        {
          id: "n_quick_agent",
          kind: "agent",
          name: "Correctness agent",
          role: "Logic and edge cases",
          modelId: "gpt-4o-mini",
          instruction:
            "Look for off-by-one errors, unhandled branches, and missing tests in changed code.",
        },
      ],
      edges: [{ id: "e_quick", from: "n_quick_orch", to: "n_quick_agent" }],
      rules: [
        {
          id: "r_quick_scope",
          title: "Stay within the diff",
          instruction:
            "Do not report pre-existing issues on lines this PR did not change.",
        },
        {
          id: "r_quick_tests",
          title: "Name the missing test",
          instruction:
            "When flagging an untested branch, describe the case the test should cover.",
          nodeId: "n_quick_agent",
        },
      ],
    },
  ]
}

let templates: ReviewTemplate[] = seedTemplates()

function cloneAll(): ReviewTemplate[] {
  return JSON.parse(JSON.stringify(templates)) as ReviewTemplate[]
}

function findIndex(id: string): number {
  return templates.findIndex((template) => template.id === id)
}

function templateId(): string {
  return `tpl_${Date.now().toString(36)}${Math.floor(Math.random() * 1e4)
    .toString(36)
    .padStart(3, "0")}`
}

export const templatesHandlers = [
  http.get(`${API_BASE}/templates`, async ({ request }) => {
    await latency(request)
    if (scenarioOf(request) === "error") {
      return errorResponse(
        500,
        "templates_unavailable",
        "Could not load review templates.",
        "The templates service did not respond.",
      )
    }
    const body: ReviewTemplateListResponse = {
      items: scenarioOf(request) === "empty" ? [] : cloneAll(),
    }
    return HttpResponse.json(body)
  }),

  http.post(`${API_BASE}/templates`, async ({ request }) => {
    await latency(request)
    if (scenarioOf(request) === "error") {
      return errorResponse(
        500,
        "template_create_failed",
        "Could not create the review template.",
      )
    }

    const input = (await request.json()) as Partial<ReviewTemplateInput>
    const name = input.name?.trim()
    if (!name) {
      return errorResponse(422, "name_required", "Give the template a name.")
    }

    const template: ReviewTemplate = {
      id: templateId(),
      name,
      description: input.description?.trim() ?? "",
      nodes: input.nodes ?? [],
      edges: input.edges ?? [],
      rules: input.rules ?? [],
      updatedAt: new Date().toISOString(),
    }
    templates = [template, ...templates]
    return HttpResponse.json(template, { status: 201 })
  }),

  http.get(`${API_BASE}/templates/:id`, async ({ request, params }) => {
    await latency(request)
    if (scenarioOf(request) === "error") {
      return errorResponse(
        500,
        "template_unavailable",
        "Could not load the review template.",
      )
    }
    const found = templates.find((template) => template.id === params.id)
    if (!found) {
      return errorResponse(
        404,
        "template_not_found",
        "That review template does not exist.",
        `No template with id "${String(params.id)}".`,
      )
    }
    return HttpResponse.json(found)
  }),

  http.patch(`${API_BASE}/templates/:id`, async ({ request, params }) => {
    await latency(request)
    if (scenarioOf(request) === "error") {
      return errorResponse(
        500,
        "template_update_failed",
        "Could not save the review template.",
      )
    }

    const index = findIndex(String(params.id))
    if (index < 0) {
      return errorResponse(
        404,
        "template_not_found",
        "That review template does not exist.",
      )
    }

    const body = (await request.json()) as Partial<ReviewTemplateInput>
    if (body.name !== undefined && !body.name.trim()) {
      return errorResponse(422, "name_required", "Give the template a name.")
    }

    const current = templates[index]
    const next: ReviewTemplate = {
      ...current,
      name: body.name !== undefined ? body.name.trim() : current.name,
      description: body.description ?? current.description,
      nodes: body.nodes ?? current.nodes,
      edges: body.edges ?? current.edges,
      rules: body.rules ?? current.rules,
      updatedAt: new Date().toISOString(),
    }
    templates = templates.map((template, i) => (i === index ? next : template))
    return HttpResponse.json(next)
  }),
]
