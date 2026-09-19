"""ORM model package.

Importing this module registers every model against ``Base.metadata`` so that
Alembic autogenerate and ``Base.metadata.create_all`` see the full schema.
"""

from slopolis_db.models.agent_event import AgentEventRow
from slopolis_db.models.agent_run import AgentRun
from slopolis_db.models.audit import AuditLog
from slopolis_db.models.finding import Finding
from slopolis_db.models.github import GitHubInstallation, Repository
from slopolis_db.models.provider import ModelAssignment, ModelCatalog, ProviderCredential
from slopolis_db.models.review import ReviewSession, SessionTarget, SessionTargetRun
from slopolis_db.models.usage import UsageRecord
from slopolis_db.models.user import User
from slopolis_db.models.workspace import Workspace

__all__ = [
    "AgentEventRow",
    "AgentRun",
    "AuditLog",
    "Finding",
    "GitHubInstallation",
    "ModelAssignment",
    "ModelCatalog",
    "ProviderCredential",
    "Repository",
    "ReviewSession",
    "SessionTarget",
    "SessionTargetRun",
    "UsageRecord",
    "User",
    "Workspace",
]
