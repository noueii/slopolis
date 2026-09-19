"""Wire schemas for the API — the Python mirror of ``apps/web/src/api/contract.ts``.

Every model serializes camelCase via :data:`app.config.CAMEL`, accepts both
camelCase and snake_case input (``populate_by_name``), and can be built from
ORM rows (``from_attributes``). These are the single source of truth for the
FastAPI OpenAPI schema the UI is allowed to depend on.
"""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, ConfigDict, Field

from app.config import CAMEL
from slopolis_core.domain import SessionStatus, TargetStatus

__all__ = [
    "ApiErrorBody",
    "CreateReviewRequest",
    "CreatedSession",
    "DashboardData",
    "DashboardParams",
    "DashboardSession",
    "DashboardSummary",
    "FilterOption",
    "LiveSession",
    "ModelCatalog",
    "ModelOption",
    "OpenPullRequest",
    "Paginated",
    "PrReference",
    "PreflightRequest",
    "PreflightResult",
    "PullRequestChecks",
    "RepositoryListResponse",
    "RepositoryPullRequestsResponse",
    "RepositoryRef",
    "RepositorySummary",
    "ReviewPreset",
    "ReviewPresetCatalog",
    "ReviewSession",
    "SessionFilterOptions",
    "SessionListParams",
    "SessionStats",
    "SessionTarget",
    "UserRef",
    "WireModel",
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


class UserRef(WireModel):
    """A GitHub user referenced by a session."""

    id: str
    handle: str
    name: str
    avatar_url: str | None = None
    is_admin: bool = False


# --- sessions ---------------------------------------------------------------


class SessionTarget(WireModel):
    """One pull request within a session, with per-target aggregates."""

    id: str
    repository: RepositoryRef
    number: int
    title: str
    url: str
    head_branch: str
    status: TargetStatus
    findings_count: int = 0
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


class RepositoryListResponse(WireModel):
    """Every connected repository for the workspace."""

    items: list[RepositorySummary]


class PullRequestChecks(WireModel):
    """Rolled-up CI status for an open pull request."""

    state: str
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
    updated_at: str
    draft: bool
    comments: int
    changed_files: int
    additions: int
    deletions: int
    checks: PullRequestChecks


class RepositoryPullRequestsResponse(WireModel):
    """Open pull requests for one connected repository."""

    repository: RepositorySummary
    pull_requests: list[OpenPullRequest]


# --- dashboard --------------------------------------------------------------


class LiveSession(WireModel):
    """A session target that is still queued or running."""

    id: str
    name: str
    status: SessionStatus
    repository: RepositoryRef
    number: int
    pr_label: str
    title: str
    url: str
    head_branch: str
    model: str
    provider: str
    progress: int
    step: str
    started_at: dt.datetime
    elapsed_ms: int


class DashboardSummary(WireModel):
    """Aggregates for the dashboard analytics strip."""

    scope: str
    total_sessions: int
    running: int
    failed: int
    spend_usd: float
    tokens: int


class DashboardSession(WireModel):
    """Compact history entry rendered as a conversation row."""

    id: str
    title: str
    name: str
    status: SessionStatus
    model: str
    provider: str
    prompt: str | None = None
    created_at: dt.datetime
    finished_at: dt.datetime | None = None
    targets: list[SessionTarget] = Field(default_factory=list)
    target_count: int = 0
    findings_count: int = 0
    cost_usd: float = 0.0


class DashboardData(WireModel):
    """Everything the Dashboard home needs in one response."""

    scope: str
    summary: DashboardSummary
    running: list[LiveSession] = Field(default_factory=list)
    recent: list[DashboardSession] = Field(default_factory=list)
    generated_at: dt.datetime


class DashboardParams(WireModel):
    """Query parameters accepted by ``GET /api/dashboard``."""

    model_config = ConfigDict(
        alias_generator=CAMEL,
        populate_by_name=True,
        extra="forbid",
    )

    repo: str | None = None
    limit: int = Field(default=12, ge=1, le=50)


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
