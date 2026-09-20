/**
 * The mock provider/catalog/assignment store: what it serves to the Providers &
 * models screen, held to the semantics the real router implements — a masked
 * key, a connection test that records its outcome (a failure included) on the
 * row, an import that counts new rows only, `auto` as the absence of an
 * assignment row, and the delete cascades.
 */

import { setupServer } from "msw/node"
import { afterAll, beforeAll, beforeEach, describe, expect, it } from "vitest"

import type {
  ApiErrorBody,
  AssignmentResponse,
  CatalogModel,
  CatalogModelListResponse,
  ModelImportResponse,
  ProviderCredential,
  ProviderListResponse,
  ProviderTestResult,
  RoleAssignment,
} from "@/api/contract"
import { providersHandlers, resetProviderStore } from "./providers"

const server = setupServer(...providersHandlers)

beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterAll(() => server.close())
beforeEach(() => resetProviderStore())

async function send(
  path: string,
  init: RequestInit = {},
  scenario?: string,
): Promise<Response> {
  const headers = new Headers(init.headers)
  if (init.body !== undefined) headers.set("Content-Type", "application/json")
  if (scenario !== undefined) headers.set("x-mock-scenario", scenario)
  return fetch(`/api${path}`, { ...init, headers })
}

async function json<T>(
  path: string,
  init: RequestInit = {},
  scenario?: string,
): Promise<T> {
  const response = await send(path, init, scenario)
  expect(response.ok).toBe(true)
  return (await response.json()) as T
}

describe("mock provider admin", () => {
  it("stores a created key and only ever returns its last four characters", async () => {
    const created = await json<ProviderCredential>("/providers", {
      method: "POST",
      body: JSON.stringify({
        provider: "openai",
        baseUrl: "https://api.openai.com/v1/",
        apiKey: "sk-proj-supersecret-1234",
      }),
    })

    expect(created).toMatchObject({
      provider: "openai",
      baseUrl: "https://api.openai.com/v1",
      keyLast4: "1234",
      enabled: true,
      lastStatus: null,
      lastCheckedAt: null,
    })

    const list = await json<ProviderListResponse>("/providers")
    expect(list.items.map((item) => item.id)).toContain(created.id)
    expect(list.items.find((item) => item.id === created.id)).toEqual(created)
    // The key never rides along on any response.
    expect(JSON.stringify(list)).not.toContain("supersecret")
    expect(list.items.every((item) => item.keyLast4.length === 4)).toBe(true)
  })

  it("rotates a key in place and adds a manual model", async () => {
    const updated = await json<ProviderCredential>("/providers/cred_litellm", {
      method: "PATCH",
      body: JSON.stringify({
        apiKey: "sk-rotated-abcd",
        enabled: false,
        baseUrl: "",
      }),
    })

    expect(updated).toMatchObject({
      id: "cred_litellm",
      keyLast4: "abcd",
      enabled: false,
      baseUrl: null,
    })

    const created = await json<CatalogModel>("/catalog/models", {
      method: "POST",
      body: JSON.stringify({ modelId: "gemini-2.5-pro", provider: "google" }),
    })
    expect(created).toMatchObject({
      modelId: "gemini-2.5-pro",
      provider: "google",
      displayName: null,
      source: "manual",
      credentialId: null,
    })

    const duplicate = await send("/catalog/models", {
      method: "POST",
      body: JSON.stringify({ modelId: "gemini-2.5-pro", provider: "google" }),
    })
    expect(duplicate.status).toBe(409)

    const catalog = await json<CatalogModelListResponse>("/catalog/models")
    expect(catalog.items.map((model) => model.modelId)).toEqual([
      "claude-sonnet-4",
      "gpt-4o-mini",
      "gemini-2.5-pro",
    ])
    // The first row is what `auto` resolves to, whatever the later adds.
    expect(catalog.defaultModelId).toBe("claude-sonnet-4")
  })

  it("counts only newly imported rows and imports nothing the second time", async () => {
    const importBody = JSON.stringify({ credentialId: "cred_litellm" })
    const first = await json<ModelImportResponse>("/catalog/models/import", {
      method: "POST",
      body: importBody,
    })

    // The provider lists three models; one is new, one was already imported, and
    // one was added by hand and is refreshed rather than created again.
    expect(first.imported).toBe(1)
    expect(first.items.map((item) => item.modelId)).toEqual([
      "claude-sonnet-4",
      "claude-opus-4",
      "gpt-4o-mini",
    ])
    expect(first.items.find((item) => item.modelId === "claude-opus-4")).toMatchObject({
      source: "import",
      provider: "litellm",
      credentialId: "cred_litellm",
    })
    expect(first.items.find((item) => item.modelId === "gpt-4o-mini")).toMatchObject({
      source: "manual",
      provider: "litellm",
      credentialId: "cred_litellm",
    })

    const after = await json<CatalogModelListResponse>("/catalog/models")
    expect(after.items.map((model) => model.modelId)).toEqual([
      "claude-sonnet-4",
      "gpt-4o-mini",
      "claude-opus-4",
    ])
    expect(after.defaultModelId).toBe("claude-sonnet-4")

    const second = await json<ModelImportResponse>("/catalog/models/import", {
      method: "POST",
      body: importBody,
    })
    expect(second.imported).toBe(0)
    expect(second.items.map((item) => item.id)).toEqual(
      first.items.map((item) => item.id),
    )
    expect(
      (await json<CatalogModelListResponse>("/catalog/models")).items,
    ).toEqual(after.items)
  })

  it("reports a model for a role it was set to and null for every other role", async () => {
    const before = await json<AssignmentResponse>("/catalog/assignments")
    expect(before.defaultModelId).toBe("claude-sonnet-4")
    expect(before.roles[0]).toEqual({
      role: "review",
      modelId: "claude-sonnet-4",
    })
    expect(before.roles.slice(1).every((row) => row.modelId === null)).toBe(true)

    const saved = await json<RoleAssignment>(
      "/catalog/assignments/review.security",
      { method: "PUT", body: JSON.stringify({ modelId: "gpt-4o-mini" }) },
    )
    expect(saved).toEqual({ role: "review.security", modelId: "gpt-4o-mini" })

    const after = await json<AssignmentResponse>("/catalog/assignments")
    const byRole = new Map(after.roles.map((row) => [row.role, row.modelId]))
    expect(byRole.get("review.security")).toBe("gpt-4o-mini")
    expect(byRole.get("review")).toBe("claude-sonnet-4")
    expect(byRole.get("review.fast")).toBeNull()

    // `auto` is the absence of a row, so clearing a role leaves no trace of it.
    const cleared = await json<RoleAssignment>(
      "/catalog/assignments/review.security",
      { method: "PUT", body: JSON.stringify({ modelId: null }) },
    )
    expect(cleared).toEqual({ role: "review.security", modelId: null })
    expect((await json<AssignmentResponse>("/catalog/assignments")).roles).toEqual(
      before.roles,
    )

    const unknownRole = await send("/catalog/assignments/nope", {
      method: "PUT",
      body: JSON.stringify({ modelId: "gpt-4o-mini" }),
    })
    expect(unknownRole.status).toBe(422)
    const unknownModel = await send("/catalog/assignments/review.fast", {
      method: "PUT",
      body: JSON.stringify({ modelId: "not-in-the-catalog" }),
    })
    expect(unknownModel.status).toBe(422)
  })

  it("records a connection test on the row and answers 200 even when it fails", async () => {
    const ok = await json<ProviderTestResult>("/providers/cred_litellm/test", {
      method: "POST",
    })
    expect(ok).toMatchObject({ status: "ok", detail: null })

    const tested = await json<ProviderListResponse>("/providers")
    expect(tested.items[0]).toMatchObject({
      lastStatus: "ok",
      lastCheckedAt: ok.checkedAt,
    })

    // A provider the mock cannot reach is a recorded status, not an API error.
    const added = await json<ProviderCredential>("/providers", {
      method: "POST",
      body: JSON.stringify({
        provider: "mystery-gateway",
        apiKey: "sk-mystery-0000",
      }),
    })
    const failed = await json<ProviderTestResult>(
      `/providers/${added.id}/test`,
      { method: "POST" },
    )
    expect(failed.status).toBe("failed")
    expect(failed.detail).toContain("mystery-gateway")

    const after = await json<ProviderListResponse>("/providers")
    expect(after.items.find((item) => item.id === added.id)).toMatchObject({
      lastStatus: "failed",
      lastCheckedAt: failed.checkedAt,
    })
  })

  it("deletes the models a credential imported and clears the roles pointing at them", async () => {
    const response = await send("/providers/cred_litellm", { method: "DELETE" })
    expect(response.status).toBe(204)

    expect(await json<ProviderListResponse>("/providers")).toEqual({ items: [] })

    const catalog = await json<CatalogModelListResponse>("/catalog/models")
    // The hand-added row survives; the imported one goes with the credential.
    expect(catalog.items.map((model) => model.modelId)).toEqual(["gpt-4o-mini"])
    expect(catalog.defaultModelId).toBe("gpt-4o-mini")

    const assignments = await json<AssignmentResponse>("/catalog/assignments")
    expect(
      assignments.roles.find((row) => row.role === "review")?.modelId,
    ).toBeNull()
    expect(assignments.roles.every((row) => row.modelId === null)).toBe(true)
  })

  it("clears an assignment when its catalog row is deleted", async () => {
    const catalog = await json<CatalogModelListResponse>("/catalog/models")
    const row = catalog.items.find(
      (model) => model.modelId === "claude-sonnet-4",
    )
    if (row === undefined) throw new Error("the seed lost its imported model")

    const response = await send(`/catalog/models/${row.id}`, {
      method: "DELETE",
    })
    expect(response.status).toBe(204)

    const assignments = await json<AssignmentResponse>("/catalog/assignments")
    expect(
      assignments.roles.find((entry) => entry.role === "review")?.modelId,
    ).toBeNull()
    expect(assignments.defaultModelId).toBe("gpt-4o-mini")
  })

  it("answers the empty scenario with empty collections, not a 404", async () => {
    expect(await json<ProviderListResponse>("/providers", {}, "empty")).toEqual({
      items: [],
    })
    expect(
      await json<CatalogModelListResponse>("/catalog/models", {}, "empty"),
    ).toEqual({ items: [], defaultModelId: null })

    const assignments = await json<AssignmentResponse>(
      "/catalog/assignments",
      {},
      "empty",
    )
    expect(assignments.defaultModelId).toBeNull()
    expect(assignments.roles.map((row) => row.role)).toEqual([
      "review",
      "harness.orchestrator",
      "review.fast",
      "review.specialist",
      "review.security",
      "review.tests",
    ])
    expect(assignments.roles.every((row) => row.modelId === null)).toBe(true)
  })

  it("fails every read under the error scenario with the API envelope", async () => {
    for (const path of ["/providers", "/catalog/models", "/catalog/assignments"]) {
      const response = await send(path, {}, "error")
      expect(response.status).toBe(500)
      const body = (await response.json()) as ApiErrorBody
      expect(body.error.code.length).toBeGreaterThan(0)
      expect(body.error.message.length).toBeGreaterThan(0)
    }
  })
})
