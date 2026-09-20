/**
 * The mock repository switches, held to the semantics the real router
 * implements (spec 10.1 / 10.10): one PATCH carries the parked state, the
 * review access rule, or both; a rule outside the three literals is refused;
 * and what a write sets is what a later read serves.
 */

import { setupServer } from "msw/node"
import { afterAll, beforeAll, describe, expect, it } from "vitest"

import type {
  ApiErrorBody,
  RepositoryListResponse,
  RepositorySummary,
} from "@/api/contract"
import { dashboardHandlers } from "./dashboard"

const server = setupServer(...dashboardHandlers)

beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterAll(() => server.close())

async function send(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers)
  if (init.body !== undefined) headers.set("Content-Type", "application/json")
  return fetch(`/api${path}`, { ...init, headers })
}

async function listRepositories(): Promise<RepositorySummary[]> {
  const response = await send("/repositories")
  const body = (await response.json()) as RepositoryListResponse
  return body.items
}

async function patchRepository(id: string, body: unknown): Promise<Response> {
  return send(`/repositories/${id}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  })
}

describe("mock repository access policy", () => {
  it("round-trips a review access change into later reads", async () => {
    const items = await listRepositories()
    const target = items[0]
    const next = target.requiredAccess === "read" ? "write" : "read"

    const response = await patchRepository(target.id, { requiredAccess: next })

    expect(response.status).toBe(200)
    const updated = (await response.json()) as RepositorySummary
    expect(updated.requiredAccess).toBe(next)

    const reread = (await listRepositories()).find(
      (item) => item.id === target.id,
    )
    expect(reread?.requiredAccess).toBe(next)
  })

  it("keeps the parked state when only the rule is patched", async () => {
    const items = await listRepositories()
    const target = items[0]

    const response = await patchRepository(target.id, {
      requiredAccess: "write",
    })

    const updated = (await response.json()) as RepositorySummary
    expect(updated.enabled).toBe(target.enabled)
  })

  it("refuses a rule outside the three literals", async () => {
    const items = await listRepositories()

    const response = await patchRepository(items[0].id, {
      requiredAccess: "sometimes",
    })

    expect(response.status).toBe(422)
    const body = (await response.json()) as ApiErrorBody
    expect(body.error.code).toBe("required_access_invalid")

    const reread = (await listRepositories()).find(
      (item) => item.id === items[0].id,
    )
    expect(reread?.requiredAccess).toBe(items[0].requiredAccess)
  })
})
