"""Wire schemas for the API — the Python mirror of ``apps/web/src/api/contract.ts``.

Every model serializes camelCase via :data:`app.config.CAMEL`, accepts both
camelCase and snake_case input (``populate_by_name``), and can be built from
ORM rows (``from_attributes``). These are the single source of truth for the
FastAPI OpenAPI schema the UI is allowed to depend on.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.config import CAMEL
from app.retry_actions import RetryAction
from slopolis_core.domain import SessionStatus, Severity, TargetStatus

__all__ = [
    "AgentEventItem",
    "AgentEventPage",
    "AgentRunNode",
    "AgentRunTreeResponse",
    "ApiErrorBody",
    "AssignmentResponse",
    "AssignmentUpdateRequest",
    "CatalogModelCreateRequest",
    "CatalogModelListResponse",
    "CatalogModelRef",
    "CreateReviewRequest",
    "CreatedSession",
    "FilterOption",
    "Finding",
    "ModelCatalog",
    "ModelImportRequest",
    "ModelImportResponse",
    "ModelOption",
    "OpenPullRequest",
    "Paginated",
    "PrReference",
    "PreflightRequest",
    "PreflightResult",
    "ProviderCreateRequest",
    "ProviderCredentialRef",
    "ProviderListResponse",
    "ProviderTestResult",
    "ProviderUpdateRequest",
    "PullRequestChecks",
    "PullRequestChecksState",
    "PullRequestFilterOptions",
    "PullRequestListItem",
    "PullRequestListParams",
    "PullRequestListResponse",
    "PullRequestReview",
    "PullRequestReviewFilter",
    "PullRequestReviewState",
    "PullRequestSort",
    "PullRequestSummary",
    "RepositoryListResponse",
    "RepositoryRef",
    "RepositorySummary",
    "RetryAction",
    "RetryRequest",
    "ReviewPreset",
    "ReviewPresetCatalog",
    "ReviewSession",
    "RoleAssignmentRef",
    "SessionFilterOptions",
    "SessionListParams",
    "SessionStats",
    "SessionTarget",
    "UserRef",
    "WireModel",
    "WorkspaceSettings",
    "WorkspaceSettingsUpdate",
]


class WireModel(BaseModel):
    """Base model that speaks camelCase on the wire and reads ORM rows."""

    model_config = ConfigDict(
        alias_generator=CAMEL,
        populate_by_name=True,
        from_attributes=True,
    )


# --- shared refs ------------------------------------------------------------


class RepositoryRef(WireModel):
    """A repository the workspace can reach."""

    id: str
    full_name: str
    private: bool
    default_branch: str | None = None


class WorkspaceRef(WireModel):
    """A workspace a user can act in."""

    id: str
    name: str
    slug: str


class WorkspaceListResponse(WireModel):
    """The workspaces the caller belongs to (v1: at most one)."""

    items: list[WorkspaceRef] = Field(default_factory=list)


class CreateWorkspaceRequest(WireModel):
    """Body of ``POST /api/workspaces``."""

    name: str


class WorkspaceSettings(WireModel):
    """The workspace's caps (spec 10.10).

    Every field is ``null`` when unset, which means unlimited: the caps are
    opt-in, so a deployment that never touches them submits exactly as it did.
    A set value is an integer >= 1.
    """

    max_concurrent_sessions: int | None = None
    max_sessions_per_user_per_day: int | None = None
    max_targets_per_repo: int | None = None
    max_targets_per_installation: int | None = None


class WorkspaceSettingsUpdate(WireModel):
    """Body of ``PATCH /api/workspaces/settings``.

    Partial on purpose: an omitted field is left as it is, while an explicit
    ``null`` clears the cap back to unlimited — the two cannot be told apart
    from the value alone, so the route reads ``model_fields_set``.
    """

    model_config = ConfigDict(
        alias_generator=CAMEL,
        populate_by_name=True,
        extra="forbid",
    )

    max_concurrent_sessions: int | None = Field(default=None, ge=1)
    max_sessions_per_user_per_day: int | None = Field(default=None, ge=1)
    max_targets_per_repo: int | None = Field(default=None, ge=1)
    max_targets_per_installation: int | None = Field(default=None, ge=1)


class UserRef(WireModel):
    """A GitHub user referenced by a session."""

    id: str
    handle: str
    name: str
    avatar_url: str | None = None
    is_admin: bool = False


class MeResponse(UserRef):
    """Identity for the signed-in caller (``GET /api/me``).

    ``workspace`` is ``None`` until the account creates or joins one — the signal
    the onboarding gate keys off. It lives here rather than on :class:`UserRef`
    so session and PR author references do not pretend to carry a workspace.
    """

    workspace: WorkspaceRef | None = None


# --- sessions ---------------------------------------------------------------


class Finding(WireModel):
    """One review finding the harness produced for a target (spec 10.6).

    ``comment_url`` is the GitHub comment this finding was posted as, and is
    ``null`` when it never got one: no diff line to anchor a comment to, a
    severity below the repository's threshold (those are summarized instead), or
    a publish that was refused or failed. The UI reads it to link a finding back
    to where it landed on the pull request.
    """

    path: str
    line: int | None = None
    severity: Severity
    category: str
    message: str
    suggestion: str | None = None
    comment_url: str | None = None
    #: The login the comment is posted as (``"<slug>[bot]"``), non-null exactly
    #: when the finding has a comment — the same condition as ``comment_url``.
    author: str | None = None
    #: When the app wrote that comment, non-null under the same condition.
    posted_at: dt.datetime | None = None
    #: GitHub's own hunk for that comment — the ``@@ … @@`` header and its lines,
    #: exactly the text GitHub renders above the comment — so the app can show the
    #: code a finding is about. Non-null under the same condition as the rest of
    #: the comment fields; null for a comment posted before the app recorded hunks.
    diff_hunk: str | None = None


class SessionTarget(WireModel):
    """One pull request within a session, with per-target aggregates."""

    id: str
    repository: RepositoryRef
    number: int
    title: str
    url: str
    head_branch: str
    status: TargetStatus
    #: What a manual retry would do (spec 10.5 §Retrying a run that only failed to
    #: publish): ``"publish"`` re-posts the review the last attempt already
    #: produced, ``"review"`` runs the model again. ``None`` means the target is
    #: not retryable, so there is nothing for the button to promise.
    retry_action: RetryAction | None = None
    findings_count: int = 0
    #: The findings themselves, most severe first. Only the session detail read
    #: fills this; the list leaves it ``null``, because a page of sessions must
    #: not carry every finding of every session.
    findings: list[Finding] | None = None
    tokens: int = 0
    cost_usd: float = 0.0
    duration_ms: int | None = None


class ReviewSession(WireModel):
    """One review submission: N PR targets plus an optional prompt."""

    id: str
    title: str
    name: str
    status: SessionStatus
    model: str
    provider: str
    triggered_by: UserRef
    created_at: dt.datetime
    started_at: dt.datetime | None = None
    finished_at: dt.datetime | None = None
    duration_ms: int | None = None
    targets: list[SessionTarget] = Field(default_factory=list)
    target_count: int = 0
    tokens: int = 0
    cost_usd: float = 0.0
    findings_count: int = 0
    prompt: str | None = None


class Paginated(WireModel):
    """A page of results plus its navigation metadata."""

    items: list[ReviewSession]
    page: int
    page_size: int
    total: int
    total_pages: int


class FilterOption(WireModel):
    """One selectable filter value."""

    value: str
    label: str
    hint: str | None = None


class SessionFilterOptions(WireModel):
    """Every filter dimension offered by the Sessions screen."""

    repositories: list[FilterOption] = Field(default_factory=list)
    users: list[FilterOption] = Field(default_factory=list)
    statuses: list[FilterOption] = Field(default_factory=list)
    models: list[FilterOption] = Field(default_factory=list)


class SessionStats(WireModel):
    """Aggregate counters for the Sessions screen header."""

    total_sessions: int
    running: int
    failed: int
    tokens: int
    cost_usd: float


class SessionListParams(WireModel):
    """Query parameters accepted by ``GET /api/sessions``.

    ``extra="forbid"`` turns a typo like ``page_size=5`` (instead of
    ``pageSize``) into a 422 instead of silently ignoring it.
    """

    model_config = ConfigDict(
        alias_generator=CAMEL,
        populate_by_name=True,
        extra="forbid",
    )

    q: str | None = None
    repo: str | None = None
    user: str | None = None
    status: SessionStatus | None = None
    range: str | None = None
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=25, ge=1, le=100)
    sort: str = "created_desc"


# --- repositories + pull requests ------------------------------------------


class RepositorySummary(WireModel):
    """A repository connected through the GitHub App installation."""

    id: str
    full_name: str
    private: bool
    default_branch: str
    open_pr_count: int
    last_activity_at: dt.datetime
    connected: bool
    #: The workspace switch: a connected repository can be parked without losing its history.
    enabled: bool = True


class RepositoryListResponse(WireModel):
    """Every connected repository for the workspace."""

    items: list[RepositorySummary]


# --- pull-request inbox (spec v3) -------------------------------------------

#: The review states a row can report (spec v3 §2).
PullRequestReviewState = Literal["never", "queued", "running", "reviewed", "failed"]
#: ``stale`` refines ``reviewed``: a review that is behind the pull request head.
PullRequestReviewFilter = Literal[
    "never", "queued", "running", "reviewed", "stale", "failed"
]
#: The CI rollup states a row can carry.
PullRequestChecksState = Literal["passing", "failing", "pending", "none"]
#: The orders the inbox accepts (spec v3 §3).
PullRequestSort = Literal["updated_desc", "size_desc", "staleness_desc", "created_desc"]


class PullRequestChecks(WireModel):
    """Rolled-up CI status for an open pull request."""

    state: PullRequestChecksState
    total: int
    passing: int


class OpenPullRequest(WireModel):
    """An open pull request discovered for a connected repository."""

    id: str
    repository: RepositoryRef
    number: int
    title: str
    url: str
    author: UserRef
    head_branch: str
    #: Commit the pull request head points at; a review is compared against it.
    head_sha: str
    updated_at: str
    draft: bool
    comments: int
    changed_files: int
    additions: int
    deletions: int
    checks: PullRequestChecks


# --- pull-request inbox (spec v3) -------------------------------------------


class PullRequestReview(WireModel):
    """What slopolis knows about an open pull request's latest review.

    ``commitsSinceReview`` is ``0`` when the review covers the current head, a
    positive count when it is behind, and ``null`` when the head has moved but
    the commits between the two SHAs could not be counted — an unknown distance,
    never an invented one.
    """

    state: PullRequestReviewState
    session_id: str | None = None
    reviewed_sha: str | None = None
    commits_since_review: int | None = 0
    findings_count: int = 0
    progress: int | None = None
    step: str | None = None
    reviewed_at: dt.datetime | None = None


class PullRequestListItem(OpenPullRequest):
    """One row of the pull-request inbox."""

    review: PullRequestReview


class PullRequestSummary(WireModel):
    """Backlog totals for the inbox header (spec v3 §3)."""

    total: int
    #: Never reviewed plus reviewed-but-behind.
    needs_review: int
    stale: int
    #: Queued plus running.
    running: int


class PullRequestFilterOptions(WireModel):
    """Every filter dimension the inbox offers, counted over its whole row set."""

    repositories: list[FilterOption] = Field(default_factory=list)
    reviews: list[FilterOption] = Field(default_factory=list)
    checks: list[FilterOption] = Field(default_factory=list)


class PullRequestListParams(WireModel):
    """Query parameters accepted by ``GET /api/pull-requests`` (spec v3 §6).

    ``extra="forbid"`` turns a typo like ``page_size=5`` into a 422 instead of a
    silently ignored filter. ``page`` and ``pageSize`` are clamped rather than
    refused: a stale page number left over from a filter change answers with the
    last page instead of an error.
    """

    model_config = ConfigDict(
        alias_generator=CAMEL,
        populate_by_name=True,
        extra="forbid",
    )

    q: str | None = None
    repo: str | None = None
    review: PullRequestReviewFilter | None = None
    checks: PullRequestChecksState | None = None
    #: Drafts are hidden unless this is set; the client sends ``drafts=1``.
    drafts: bool = False
    page: int = 1
    page_size: int = 25
    sort: PullRequestSort = "updated_desc"


class PullRequestListResponse(WireModel):
    """``GET /api/pull-requests``: the page plus what the header and bar need."""

    items: list[PullRequestListItem] = Field(default_factory=list)
    page: int
    page_size: int
    total: int
    total_pages: int
    summary: PullRequestSummary
    filter_options: PullRequestFilterOptions
    generated_at: dt.datetime


# --- preflight + create -----------------------------------------------------


class PrReference(WireModel):
    """One PR resolved from a pasted link."""

    url: str
    repository: RepositoryRef
    number: int
    title: str


class PreflightRequest(WireModel):
    """Body of ``POST /api/reviews/preflight``."""

    model_config = ConfigDict(
        alias_generator=CAMEL,
        populate_by_name=True,
        extra="forbid",
    )

    pr_urls: list[str] = Field(default_factory=list)


class PreflightResult(WireModel):
    """Valid/invalid split plus human-readable notices."""

    valid: list[PrReference] = Field(default_factory=list)
    invalid: list[str] = Field(default_factory=list)
    notices: list[str] = Field(default_factory=list)


class ModelOption(WireModel):
    """One selectable model in the workspace catalog."""

    id: str
    provider: str


class ModelCatalog(WireModel):
    """Workspace model selection for New Review."""

    default_model_id: str
    default_provider: str
    models: list[ModelOption] = Field(default_factory=list)


class ReviewPreset(WireModel):
    """One review/orchestration preset."""

    id: str
    name: str
    description: str


class ReviewPresetCatalog(WireModel):
    """Available presets plus the workspace default."""

    default_preset_id: str
    presets: list[ReviewPreset] = Field(default_factory=list)


class ReviewAttachment(WireModel):
    """Metadata for an image attached to a review request (never bytes)."""

    id: str
    name: str
    mime: str
    size: int


class CreateReviewRequest(WireModel):
    """Body of ``POST /api/sessions``."""

    model_config = ConfigDict(
        alias_generator=CAMEL,
        populate_by_name=True,
        extra="forbid",
    )

    pr_urls: list[str] = Field(default_factory=list)
    prompt: str | None = None
    preset: str | None = None
    attachments: list[ReviewAttachment] = Field(default_factory=list)


class CreatedSession(WireModel):
    """The session produced by a successful submission."""

    id: str
    title: str
    name: str
    status: SessionStatus
    model: str
    provider: str
    created_at: dt.datetime
    target_count: int
    prompt: str | None = None


class SessionUpdateRequest(WireModel):
    """Body of ``PATCH /api/sessions/{id}``."""

    model_config = ConfigDict(
        alias_generator=CAMEL,
        populate_by_name=True,
        extra="forbid",
    )

    title: str | None = None
    name: str | None = None


class RetryRequest(WireModel):
    """Body of ``POST /api/sessions/{id}/retry`` (spec 10.5 §Manual retry).

    An absent or empty ``targetIds`` means every target in a retryable state,
    which is what the detail screen's plain "Retry" sends.
    """

    model_config = ConfigDict(
        alias_generator=CAMEL,
        populate_by_name=True,
        extra="forbid",
    )

    target_ids: list[uuid.UUID] | None = None


# --- usage ------------------------------------------------------------------


class UsageBreakdown(WireModel):
    """Usage attributed to one dimension value (model, repository, …)."""

    key: str
    label: str
    tokens: int
    cost_usd: float
    sessions: int


class UsagePoint(WireModel):
    """One bucket of the usage time series."""

    date: dt.date
    tokens: int
    cost_usd: float
    sessions: int


class UsageResponse(WireModel):
    """Totals, breakdowns, and a time series for the usage view."""

    total_tokens: int
    total_cost_usd: float
    total_sessions: int
    by_model: list[UsageBreakdown] = Field(default_factory=list)
    by_repository: list[UsageBreakdown] = Field(default_factory=list)
    by_user: list[UsageBreakdown] = Field(default_factory=list)
    series: list[UsagePoint] = Field(default_factory=list)


# --- errors -----------------------------------------------------------------


class ApiErrorDetail(WireModel):
    """The ``error`` object inside the standard error envelope."""

    code: str
    message: str
    detail: str | None = None


class ApiErrorBody(WireModel):
    """Standard error envelope returned on every non-2xx response."""

    error: ApiErrorDetail


# --- provider & model configuration ----------------------------------------


class ProviderCredentialRef(WireModel):
    """A BYOK credential as the admin screen sees it — never the key itself."""

    id: str
    provider: str
    base_url: str | None = None
    key_last4: str
    enabled: bool
    last_status: str | None = None
    last_checked_at: dt.datetime | None = None
    created_at: dt.datetime


class ProviderListResponse(WireModel):
    """Every provider credential the workspace holds."""

    items: list[ProviderCredentialRef] = Field(default_factory=list)


class ProviderCreateRequest(WireModel):
    """Body of ``POST /api/providers``; the key is write-only."""

    provider: str
    base_url: str | None = None
    api_key: str


class ProviderUpdateRequest(WireModel):
    """Body of ``PATCH /api/providers/{id}``; omitted fields stay as they are."""

    base_url: str | None = None
    api_key: str | None = None
    enabled: bool | None = None


class ProviderTestResult(WireModel):
    """Outcome of ``POST /api/providers/{id}/test``.

    An unreachable provider is a recorded status, not an API error, so the
    response is still 200 with ``status: "failed"``.
    """

    status: str
    detail: str | None = None
    checked_at: dt.datetime


class CatalogModelRef(WireModel):
    """One model in the workspace catalog."""

    id: str
    model_id: str
    provider: str
    display_name: str | None = None
    source: str
    credential_id: str | None = None


class CatalogModelListResponse(WireModel):
    """The workspace catalog plus the model ``auto`` resolves to."""

    items: list[CatalogModelRef] = Field(default_factory=list)
    default_model_id: str | None = None


class CatalogModelCreateRequest(WireModel):
    """Body of ``POST /api/catalog/models``; always creates a ``manual`` row."""

    model_id: str
    provider: str
    display_name: str | None = None


class ModelImportRequest(WireModel):
    """Body of ``POST /api/catalog/models/import``."""

    credential_id: str


class ModelImportResponse(WireModel):
    """``imported`` counts newly created rows; refreshed rows are not counted."""

    imported: int
    items: list[CatalogModelRef] = Field(default_factory=list)


class RoleAssignmentRef(WireModel):
    """One role's model choice; ``modelId: null`` means ``auto``."""

    role: str
    model_id: str | None = None


class AssignmentResponse(WireModel):
    """The workspace default plus one entry per assignable role."""

    default_model_id: str | None = None
    roles: list[RoleAssignmentRef] = Field(default_factory=list)


class AssignmentUpdateRequest(WireModel):
    """Body of ``PUT /api/catalog/assignments/{role}``; ``null`` means ``auto``."""

    model_id: str | None = None


# --- agent runs (harness V1) ------------------------------------------------


class AgentRunNode(WireModel):
    """One node of a session's run tree (spec v2 §7): main → PR → sub-agent."""

    id: uuid.UUID
    session_id: uuid.UUID
    target_id: uuid.UUID | None = None
    parent_run_id: uuid.UUID | None = None
    level: str
    role: str
    model_id: str | None = None
    objective: str
    status: str
    tokens: int
    cost_usd: float
    started_at: dt.datetime | None = None
    ended_at: dt.datetime | None = None
    error: str | None = None
    children: list[AgentRunNode] = Field(default_factory=list)


class AgentRunTreeResponse(WireModel):
    """The roots of a session's run tree; normally just the session's main run."""

    runs: list[AgentRunNode] = Field(default_factory=list)


class AgentEventItem(WireModel):
    """One persisted run event, as the replay endpoint and the SSE stream carry it."""

    id: uuid.UUID
    run_id: uuid.UUID
    parent_run_id: uuid.UUID | None = None
    seq: int
    type: str
    payload: dict[str, Any]
    created_at: dt.datetime


class AgentEventPage(WireModel):
    """A page of a run's events; ``next_seq`` is the cursor for the next call."""

    items: list[AgentEventItem] = Field(default_factory=list)
    next_seq: int | None = None
