"""Pre-flight validation: models, ports, and service (spec 10.3)."""

from slopolis_core.preflight.models import (
    PreflightOutcome,
    PreflightRequest,
    PrReference,
    RepositoryRef,
)
from slopolis_core.preflight.ports import (
    GitHubGateway,
    LiveModelCheck,
    LlmLiveModelCheck,
    WorkspaceConfigProvider,
)
from slopolis_core.preflight.service import PreflightService

__all__ = [
    "GitHubGateway",
    "LiveModelCheck",
    "LlmLiveModelCheck",
    "PrReference",
    "PreflightOutcome",
    "PreflightRequest",
    "PreflightService",
    "RepositoryRef",
    "WorkspaceConfigProvider",
]
