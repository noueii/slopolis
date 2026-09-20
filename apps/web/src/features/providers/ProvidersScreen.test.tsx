import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { ApiError, api } from "@/api/client"
import type * as client from "@/api/client"
import type {
  CatalogModel,
  ProviderCredential,
  ProviderTestResult,
  RoleAssignment,
} from "@/api/contract"
import { ProvidersScreen } from "./ProvidersScreen"

vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof client>()
  return {
    isMockModeEnabled: () => false,
    ApiError: class ApiError extends Error {
      readonly status: number
      readonly code: string

      constructor(status: number, code: string, message: string) {
        super(message)
        this.name = "ApiError"
        this.status = status
        this.code = code
      }
    },
    api: {
      listProviders: vi.fn(),
      createProvider: vi.fn(),
      updateProvider: vi.fn(),
      deleteProvider: vi.fn(),
      testProvider: vi.fn(),
      listCatalogModels: vi.fn(),
      addCatalogModel: vi.fn(),
      importCatalogModels: vi.fn(),
      deleteCatalogModel: vi.fn(),
      getAssignments: vi.fn(),
      setAssignment: vi.fn(),
    },
  }
})

const litellm: ProviderCredential = {
  id: "cred_1",
  provider: "litellm",
  baseUrl: "https://litellm.internal/v1",
  keyLast4: "9f2c",
  enabled: true,
  lastStatus: "ok",
  lastCheckedAt: "2026-09-19T10:00:00.000Z",
  createdAt: "2026-09-01T09:00:00.000Z",
}

const compatible: ProviderCredential = {
  id: "cred_2",
  provider: "acme-gateway",
  baseUrl: null,
  keyLast4: "0011",
  enabled: false,
  lastStatus: null,
  lastCheckedAt: null,
  createdAt: "2026-09-10T09:00:00.000Z",
}

const importedModel: CatalogModel = {
  id: "cat_1",
  modelId: "gpt-4o",
  provider: "litellm",
  displayName: "GPT-4o",
  source: "import",
  credentialId: "cred_1",
}

const manualModel: CatalogModel = {
  id: "cat_2",
  modelId: "claude-3-7-sonnet",
  provider: "anthropic",
  displayName: null,
  source: "manual",
  credentialId: null,
}

const roles: RoleAssignment[] = [
  { role: "review", modelId: "gpt-4o" },
  { role: "harness.orchestrator", modelId: null },
  { role: "review.security", modelId: null },
]

const testResult: ProviderTestResult = {
  status: "ok",
  detail: "3 models available",
  checkedAt: "2026-09-20T08:00:00.000Z",
}

interface StubOptions {
  credentials?: ProviderCredential[]
  models?: CatalogModel[]
  defaultModelId?: string | null
  roles?: RoleAssignment[]
  failure?: Error
}

/**
 * Every resource the screen loads is stubbed up front: with `restoreMocks` the
 * previous test's values are gone, and a missing stub would surface as a load
 * failure instead of the state under test.
 */
function stubAdminApi(options: StubOptions = {}) {
  const credentials = options.credentials ?? [litellm, compatible]
  const models = options.models ?? [importedModel, manualModel]
  const defaultModelId =
    options.defaultModelId === undefined ? "gpt-4o" : options.defaultModelId
  const roleAssignments = options.roles ?? roles

  if (options.failure !== undefined) {
    vi.mocked(api.listProviders).mockRejectedValue(options.failure)
    vi.mocked(api.listCatalogModels).mockRejectedValue(options.failure)
    vi.mocked(api.getAssignments).mockRejectedValue(options.failure)
    return
  }

  vi.mocked(api.listProviders).mockResolvedValue({ items: credentials })
  vi.mocked(api.listCatalogModels).mockResolvedValue({
    items: models,
    defaultModelId,
  })
  vi.mocked(api.getAssignments).mockResolvedValue({
    defaultModelId,
    roles: roleAssignments,
  })

  vi.mocked(api.createProvider).mockResolvedValue(litellm)
  vi.mocked(api.updateProvider).mockResolvedValue(litellm)
  vi.mocked(api.deleteProvider).mockResolvedValue(undefined)
  vi.mocked(api.testProvider).mockResolvedValue(testResult)
  vi.mocked(api.addCatalogModel).mockResolvedValue(manualModel)
  vi.mocked(api.importCatalogModels).mockResolvedValue({
    imported: 2,
    items: [importedModel, manualModel],
  })
  vi.mocked(api.deleteCatalogModel).mockResolvedValue(undefined)
  vi.mocked(api.setAssignment).mockImplementation(async (role, modelId) => ({
    role,
    modelId,
  }))
}

afterEach(cleanup)

describe("ProvidersScreen", () => {
  it("lists credentials by provider, endpoint, key last four, and last test", async () => {
    stubAdminApi()
    render(<ProvidersScreen />)

    const row = await screen.findByRole("listitem", {
      name: "Credential litellm ending 9f2c",
    })
    expect(within(row).getByText("litellm")).toBeDefined()
    expect(within(row).getByText("••••9f2c")).toBeDefined()
    expect(within(row).getByText("https://litellm.internal/v1")).toBeDefined()
    expect(within(row).getByText("Enabled")).toBeDefined()
    expect(within(row).getByText(/Last test passed/)).toBeDefined()

    // A key that never left the server cannot appear here.
    expect(within(row).queryByText(/sk-/)).toBeNull()

    const untested = screen.getByRole("listitem", {
      name: "Credential acme-gateway ending 0011",
    })
    expect(within(untested).getByText("provider default endpoint")).toBeDefined()
    expect(within(untested).getByText("Disabled")).toBeDefined()
    expect(within(untested).getByText("Never tested")).toBeDefined()
  })

  it("adds the typed credential through a write-only key field and reloads", async () => {
    stubAdminApi()
    render(<ProvidersScreen />)
    await screen.findByRole("listitem", {
      name: "Credential litellm ending 9f2c",
    })

    fireEvent.click(screen.getByRole("button", { name: "Add credential" }))
    fireEvent.change(screen.getByLabelText("Provider"), {
      target: { value: "openrouter" },
    })
    fireEvent.change(screen.getByLabelText("Base URL (optional)"), {
      target: { value: "https://openrouter.ai/api/v1" },
    })
    const keyField = screen.getByLabelText("API key")
    expect((keyField as HTMLInputElement).type).toBe("password")
    fireEvent.change(keyField, { target: { value: "sk-test-1234" } })
    fireEvent.click(screen.getByRole("button", { name: "Save credential" }))

    await waitFor(() =>
      expect(api.createProvider).toHaveBeenCalledWith({
        provider: "openrouter",
        baseUrl: "https://openrouter.ai/api/v1",
        apiKey: "sk-test-1234",
      }),
    )
    await waitFor(() => expect(api.listProviders).toHaveBeenCalledTimes(2))
  })

  it("shows the status the connection test recorded", async () => {
    stubAdminApi()
    vi.mocked(api.testProvider).mockResolvedValue({
      status: "failed",
      detail: "connect ECONNREFUSED 10.0.0.4:4000",
      checkedAt: "2026-09-20T08:00:00.000Z",
    })
    render(<ProvidersScreen />)

    const row = await screen.findByRole("listitem", {
      name: "Credential litellm ending 9f2c",
    })
    fireEvent.click(within(row).getByRole("button", { name: "Test connection" }))

    expect(
      await within(row).findByText(/connect ECONNREFUSED 10.0.0.4:4000/),
    ).toBeDefined()
    expect(within(row).getByText(/Last test failed/)).toBeDefined()
    expect(api.testProvider).toHaveBeenCalledWith("cred_1")
    await waitFor(() => expect(api.listProviders).toHaveBeenCalledTimes(2))
  })

  it("reports how many models an import newly added", async () => {
    stubAdminApi()
    render(<ProvidersScreen />)
    await screen.findByRole("listitem", { name: "Model gpt-4o" })

    fireEvent.click(screen.getByRole("button", { name: "Import from provider" }))
    fireEvent.change(screen.getByLabelText("Provider credential"), {
      target: { value: "cred_1" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Import models" }))

    expect(await screen.findByText("2 new models imported.")).toBeDefined()
    expect(api.importCatalogModels).toHaveBeenCalledWith("cred_1")
    await waitFor(() => expect(api.listCatalogModels).toHaveBeenCalledTimes(2))
  })

  it("saves the chosen model for a role, and null for Auto", async () => {
    stubAdminApi()
    render(<ProvidersScreen />)

    const review = await screen.findByLabelText("Review")
    expect(
      within(review).getByRole("option", {
        name: "Auto — workspace default (gpt-4o)",
      }),
    ).toBeDefined()
    fireEvent.change(review, { target: { value: "claude-3-7-sonnet" } })
    await waitFor(() =>
      expect(api.setAssignment).toHaveBeenCalledWith(
        "review",
        "claude-3-7-sonnet",
      ),
    )
    // The choice stays on screen while the reload is in flight.
    expect((review as HTMLSelectElement).value).toBe("claude-3-7-sonnet")

    fireEvent.change(screen.getByLabelText("Security review"), {
      target: { value: "" },
    })
    await waitFor(() =>
      expect(api.setAssignment).toHaveBeenCalledWith("review.security", null),
    )
  })

  it("explains a refused assignment and restores the stored model", async () => {
    stubAdminApi()
    vi.mocked(api.setAssignment).mockRejectedValue(
      new ApiError(
        422,
        "unknown_model",
        "Model claude-3-99 is not in the workspace catalog.",
      ),
    )
    render(<ProvidersScreen />)

    const review = await screen.findByLabelText("Review")
    fireEvent.change(review, { target: { value: "claude-3-7-sonnet" } })

    expect(
      await screen.findByText(
        "Model claude-3-99 is not in the workspace catalog.",
      ),
    ).toBeDefined()
    await waitFor(() => expect((review as HTMLSelectElement).value).toBe("gpt-4o"))
  })

  it("replaces the base URL and toggles a credential without resending the key", async () => {
    stubAdminApi()
    render(<ProvidersScreen />)

    const row = await screen.findByRole("listitem", {
      name: "Credential litellm ending 9f2c",
    })
    fireEvent.click(within(row).getByRole("button", { name: "Edit" }))
    fireEvent.change(screen.getByLabelText("Base URL"), {
      target: { value: "https://proxy.internal/v1" },
    })
    fireEvent.click(screen.getByLabelText("Enabled"))
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }))

    await waitFor(() =>
      expect(api.updateProvider).toHaveBeenCalledWith("cred_1", {
        baseUrl: "https://proxy.internal/v1",
        enabled: false,
      }),
    )
  })

  it("confirms a credential deletion and says the imported models go with it", async () => {
    stubAdminApi()
    render(<ProvidersScreen />)

    const row = await screen.findByRole("listitem", {
      name: "Credential litellm ending 9f2c",
    })
    fireEvent.click(within(row).getByRole("button", { name: "Delete" }))

    expect(await screen.findByText(/the 1 model imported through it/)).toBeDefined()
    expect(api.deleteProvider).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole("button", { name: "Delete credential" }))
    await waitFor(() => expect(api.deleteProvider).toHaveBeenCalledWith("cred_1"))
  })

  it("sends nothing until a credential exists and explains what that blocks", async () => {
    stubAdminApi({ credentials: [] })
    render(<ProvidersScreen />)

    expect(await screen.findByText("No credentials yet")).toBeDefined()
    expect(
      screen.getByText(/pre-flight refuses to create a session/),
    ).toBeDefined()
    const importButton = screen.getByRole("button", {
      name: "Import from provider",
    }) as HTMLButtonElement
    expect(importButton.disabled).toBe(true)
  })

  it("keeps the role selects usable when the catalog is empty", async () => {
    stubAdminApi({ models: [], defaultModelId: null })
    render(<ProvidersScreen />)

    const review = await screen.findByLabelText("Review")
    expect(
      within(review).getByRole("option", {
        name: "Auto — no default model yet",
      }),
    ).toBeDefined()
    expect(screen.getByText("The catalog is empty")).toBeDefined()
  })

  it("explains a deployment that has no vault to store keys in", async () => {
    stubAdminApi({
      failure: new ApiError(
        503,
        "vault_not_configured",
        "vault_not_configured",
      ),
    })
    render(<ProvidersScreen />)

    expect(await screen.findByText(/Set ENCRYPTION_KEY on the server/)).toBeDefined()
    expect(screen.queryByRole("button", { name: "Add credential" })).toBeNull()
  })

  it("explains that only admins can change providers", async () => {
    stubAdminApi({
      failure: new ApiError(403, "admin_required", "admin_required"),
    })
    render(<ProvidersScreen />)

    expect(
      await screen.findByText(/Only workspace admins can manage/),
    ).toBeDefined()
  })
})
