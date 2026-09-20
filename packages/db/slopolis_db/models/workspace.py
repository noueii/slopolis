"""Workspace model — the self-hosted tenant boundary."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from slopolis_db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from slopolis_db.models.github import GitHubInstallation, Repository
    from slopolis_db.models.provider import ModelAssignment, ModelCatalog, ProviderCredential
    from slopolis_db.models.review import ReviewSession
    from slopolis_db.models.user import User


class Workspace(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A single tenant: v1 self-hosting serves one workspace per deployment."""

    __tablename__ = "workspaces"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)

    #: Stopgap caps (spec 10.10), enforced by the submit path. ``None`` is
    #: unlimited, so a cap is opt-in and an existing deployment keeps behaving
    #: exactly as it did.
    max_concurrent_sessions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_sessions_per_user_per_day: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    #: Queue concurrency limits (spec 10.10), read only where jobs run.
    max_targets_per_repo: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_targets_per_installation: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )

    users: Mapped[list[User]] = relationship(back_populates="workspace")
    installations: Mapped[list[GitHubInstallation]] = relationship(back_populates="workspace")
    repositories: Mapped[list[Repository]] = relationship(back_populates="workspace")
    credentials: Mapped[list[ProviderCredential]] = relationship(back_populates="workspace")
    model_catalog: Mapped[list[ModelCatalog]] = relationship(back_populates="workspace")
    model_assignments: Mapped[list[ModelAssignment]] = relationship(back_populates="workspace")
    review_sessions: Mapped[list[ReviewSession]] = relationship(back_populates="workspace")
