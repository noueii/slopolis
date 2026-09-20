/**
 * slopolis API contract (v1).
 *
 * This file is the single source of truth the UI is allowed to depend on.
 * The same shapes will be produced by the FastAPI OpenAPI schema in later
 * phases; the MSW layer in `src/mocks/` only *serves* these shapes and is
 * never imported by UI code.
 */

/** Lifecycle of a whole review session (spec 10.5 / 10.8). */
export type SessionStatus = "queued" | "running" | "done" | "failed" | "cancelled"

/** Lifecycle of one PR target inside a session. */
export type TargetStatus =
  | "queued"
  | "running"
  | "done"
  | "failed"
  | "cancelled"
  | "skipped"

/** Finding severities produced by the review harness (spec 10.6). */
export type Severity = "info" | "warning" | "error" | "critical"

export interface RepositoryRef {
  id: string
  /** `owner/name`, e.g. `acme/api-gateway`. */
  fullName: string
  private: boolean
  defaultBranch?: string
}

export interface UserRef {
  id: string
  /** GitHub login without the leading `@`. */
  handle: string
  name: string
  avatarUrl?: string
  /** Whether this user holds workspace admin privileges. */
  isAdmin: boolean
}

/** A workspace an account belongs to (or is about to create). */
export interface WorkspaceRef {
  id: string
  name: string
  /** URL-safe identifier, e.g. `acme-labs`. */
  slug: string
}

/**
 * `GET /api/me`: the signed-in account plus the workspace it belongs to.
 * `workspace` is `null` for an account that has not created or joined one yet —
 * the app shows the onboarding gate instead of the shell in that case.
 */
export interface MeResponse extends UserRef {
  workspace: WorkspaceRef | null
}

/** `GET /api/workspaces`: the workspaces the account can see. */
export interface WorkspaceListResponse {
  items: WorkspaceRef[]
}

/** `POST /api/workspaces` body. */
export interface CreateWorkspaceRequest {
  name: string
}

/** One pull request within a session. */
export interface SessionTarget {
  id: string
  repository: RepositoryRef
  /** PR number. */
  number: number
  title: string
  url: string
  /** GitHub head ref for the PR, e.g. `fix/guard-token-refresh`. */
  headBranch: string
  status: TargetStatus
  findingsCount: number
  tokens: number
  costUsd: number
  durationMs?: number
}

/** A single submission: one or more PRs plus an optional prompt. */
export interface ReviewSession {
  id: string
  /** Short, agent-assigned review title, e.g. `Guard token refresh skew`. */
  title: string
  /** Auto-named from targets, e.g. `acme/api-gateway#142 +2 more`. */
  name: string
  status: SessionStatus
  /** Model id assigned to the built-in review role. */
  model: string
  /** Display label for the provider behind the model. */
  provider: string
  triggeredBy: UserRef
  createdAt: string
  startedAt?: string
  finishedAt?: string
  durationMs?: number
  targets: SessionTarget[]
  /** Convenience aggregates over `targets`. */
  targetCount: number
  tokens: number
  costUsd: number
  findingsCount: number
  prompt?: string
}

export type SessionSort =
  | "created_desc"
  | "created_asc"
  | "cost_desc"
  | "tokens_desc"

export type DateRangePreset = "all" | "24h" | "7d" | "30d" | "90d"

export interface SessionListParams {
  /** Free-text search across session name, repos, PRs and users. */
  q?: string
  /** Repository full name, or omitted for all. */
  repo?: string
  /** User handle, or omitted for all. */
  user?: string
  status?: SessionStatus
  range?: DateRangePreset
  page?: number
  pageSize?: number
  sort?: SessionSort
}

export interface Paginated<T> {
  items: T[]
  page: number
  pageSize: number
  total: number
  totalPages: number
}

export interface FilterOption {
  value: string
  label: string
  /** Optional right-aligned hint, e.g. a count or PR count. */
  hint?: string
}

export interface SessionFilterOptions {
  repositories: FilterOption[]
  users: FilterOption[]
  statuses: FilterOption[]
  models: FilterOption[]
}

export interface SessionStats {
  totalSessions: number
  running: number
  failed: number
  tokens: number
  costUsd: number
}

/** A repository connected through the GitHub App installation (spec 10.1). */
export interface RepositorySummary {
  id: string
  /** `owner/name`, e.g. `acme/api-gateway`. */
  fullName: string
  private: boolean
  defaultBranch: string
  /** Open pull requests discovered for this repository. */
  openPrCount: number
  lastActivityAt: string
  /** Whether the GitHub App can still read the repository. */
  connected: boolean
}

export interface RepositoryListResponse {
  items: RepositorySummary[]
}

/** Rolled-up CI status for an open pull request. */
export interface PullRequestChecks {
  state: "passing" | "failing" | "pending" | "none"
  /** Total check runs reported for the PR head. */
  total: number
  /** How many of them succeeded. */
  passing: number
}

/**
 * An open pull request discovered for a connected repository. This is the
 * primary selection surface for New Review (spec 10.1 / 10.4); pasting a URL
 * remains a secondary affordance that resolves to the same shape.
 */
export interface OpenPullRequest {
  id: string
  repository: RepositoryRef
  number: number
  title: string
  url: string
  author: UserRef
  updatedAt: string
  draft: boolean
  comments: number
  changedFiles: number
  additions: number
  deletions: number
  checks: PullRequestChecks
}

/** Open pull requests for one connected repository. */
export interface RepositoryPullRequestsResponse {
  repository: RepositorySummary
  /** Open PRs, most recently updated first. */
  pullRequests: OpenPullRequest[]
}

/** A session target that is still executing (spec 10.5 / 10.8). */
export interface LiveSession {
  id: string
  name: string
  status: Extract<SessionStatus, "queued" | "running">
  repository: RepositoryRef
  number: number
  /** `owner/name#123` convenience label. */
  prLabel: string
  /** Short, agent-assigned review title; the row's primary label. */
  title: string
  /** Canonical GitHub URL for the pull request. */
  url: string
  /** GitHub head ref for the PR, e.g. `fix/guard-token-refresh`. */
  headBranch: string
  model: string
  provider: string
  /** Whole-session completion, 0–100. */
  progress: number
  /** Human-readable current step, e.g. `Scanning diff (3/5 files)`. */
  step: string
  startedAt: string
  /** Elapsed wall-clock time at response time; the UI may keep ticking. */
  elapsedMs: number
}

/** Aggregates for the dashboard analytics strip (spec 10.9 usage). */
export interface DashboardSummary {
  /** Echoes the active scope, e.g. `All repositories` or `acme/api-gateway`. */
  scope: string
  totalSessions: number
  running: number
  failed: number
  spendUsd: number
  /** Total tokens attributed across the scope. */
  tokens: number
}

/** Compact history entry rendered as a conversation row (spec 10.8). */
export interface DashboardSession {
  id: string
  /** Short, agent-assigned review title, e.g. `Guard token refresh skew`. */
  title: string
  name: string
  status: SessionStatus
  model: string
  provider: string
  prompt?: string
  createdAt: string
  finishedAt?: string
  targets: SessionTarget[]
  targetCount: number
  findingsCount: number
  costUsd: number
}

export interface DashboardData {
  scope: string
  summary: DashboardSummary
  /** Only `queued`/`running` sessions, newest first. */
  running: LiveSession[]
  /** Recent sessions in scope, newest first. */
  recent: DashboardSession[]
  generatedAt: string
}

export interface DashboardParams {
  /** Repository full name, or omitted for every connected repository. */
  repo?: string
  /** Max history entries to return. */
  limit?: number
}

/** One PR resolved from a pasted link. */
export interface PrReference {
  url: string
  repository: RepositoryRef
  number: number
  title: string
}

export interface PreflightRequest {
  prUrls: string[]
}

export interface PreflightResult {
  valid: PrReference[]
  invalid: string[]
  /** Duplicate links collapsed, cross-repo notices, unknown repos, etc. */
  notices: string[]
}

/** One selectable model in the workspace catalog (spec 10.2). */
export interface ModelOption {
  id: string
  provider: string
}

/**
 * Workspace model selection for New Review. `auto` is a UI-level sentinel that
 * resolves to `defaultModelId` on the server.
 */
export interface ModelCatalog {
  /** Model id `auto` resolves to. */
  defaultModelId: string
  defaultProvider: string
  models: ModelOption[]
}

/** One review/orchestration preset (spec: future orchestration tab). */
export interface ReviewPreset {
  id: string
  name: string
  description: string
}

export interface ReviewPresetCatalog {
  /** Preset id used when none is chosen. */
  defaultPresetId: string
  presets: ReviewPreset[]
}

/**
 * Metadata for an image attached to a review request. Bytes are never sent to
 * this API — the client keeps a local object URL for previews only.
 */
export interface ReviewAttachment {
  id: string
  name: string
  /** MIME type, e.g. `image/png`. */
  mime: string
  /** Size in bytes. */
  size: number
}

export interface CreateReviewRequest {
  prUrls: string[]
  prompt?: string
  /** Review preset id, or omitted for the workspace default. */
  preset?: string
  /** Attached reference images (metadata only; never bytes). */
  attachments?: ReviewAttachment[]
}

/** The session produced by a successful submission. */
export interface CreatedSession {
  id: string
  /** Short, agent-assigned review title, e.g. `Guard token refresh skew`. */
  title: string
  name: string
  status: SessionStatus
  model: string
  provider: string
  createdAt: string
  targetCount: number
  prompt?: string
}

/** Standard error envelope returned by the API on non-2xx responses. */
export interface ApiErrorBody {
  error: {
    code: string
    message: string
    detail?: string
  }
}

/** Roles a node can play inside a review harness graph. */
export type HarnessNodeKind = "orchestrator" | "agent"

/**
 * One participant in a review harness: an orchestrator that delegates, or a
 * sub-agent that receives delegated work. `modelId`/`instruction` are optional
 * so a partially configured harness still validates.
 */
export interface HarnessNode {
  id: string
  kind: HarnessNodeKind
  name: string
  role?: string
  modelId?: string
  instruction?: string
}

/** A delegation edge from one harness node to another. */
export interface HarnessEdge {
  id: string
  from: string
  to: string
}

/** One rule the harness enforces, optionally scoped to a single node. */
export interface HarnessRule {
  id: string
  title: string
  instruction: string
  /** Owning node id, or omitted when the rule applies harness-wide. */
  nodeId?: string
}

/** A reusable review harness: an agent graph plus its rules. */
export interface ReviewTemplate {
  id: string
  name: string
  description: string
  nodes: HarnessNode[]
  edges: HarnessEdge[]
  rules: HarnessRule[]
  updatedAt: string
}

/** Mutable fields accepted when creating or updating a template. */
export type ReviewTemplateInput = Omit<ReviewTemplate, "id" | "updatedAt">

export interface ReviewTemplateListResponse {
  items: ReviewTemplate[]
}
