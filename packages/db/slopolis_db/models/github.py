"""GitHub App installation and repository models."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import TYPE_CHECKING, Literal

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from slopolis_db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from slopolis_db.models.workspace import Workspace

#: The access policies a repository can be held to (spec 10.10): ``default`` is
#: the spec rule (private repos need read, public repos need write), ``read``
#: loosens it, ``write`` tightens it. The one vocabulary the column, the wire
#: schema, and the pre-flight override all name.
type RequiredAccess = Literal["default", "read", "write"]


class GitHubInstallation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A GitHub App installation (user or organization account)."""

    __tablename__ = "github_installations"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    installation_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True)
    account_login: Mapped[str] = mapped_column(String(255), nullable=False)
    account_type: Mapped[str] = mapped_column(String(50), nullable=False)

    workspace: Mapped[Workspace] = relationship(back_populates="installations")
    repositories: Mapped[list[Repository]] = relationship(
        back_populates="installation",
        cascade="all, delete-orphan",
    )


class Repository(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A repository reachable through an installation."""

    __tablename__ = "repositories"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    installation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("github_installations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    github_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    full_name: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)
    private: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    default_branch: Mapped[str] = mapped_column(String(255), nullable=False)
    connected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    #: The workspace's own switch (spec 10.1): ``connected`` says GitHub still
    #: grants the repository, ``enabled`` says this workspace still reviews it.
    #: A parked row keeps its sessions and findings and is refused at pre-flight.
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    #: Per-repository override of the triggering access rule (spec 10.10):
    #: ``default`` applies the spec rule (private needs read, public needs
    #: write), ``read`` loosens it, ``write`` tightens it. It lives on the row
    #: rather than in a global table because it is a property of this repository.
    required_access: Mapped[RequiredAccess] = mapped_column(
        String(20),
        nullable=False,
        default="default",
        server_default="default",
    )
    last_activity_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    workspace: Mapped[Workspace] = relationship(back_populates="repositories")
    installation: Mapped[GitHubInstallation] = relationship(back_populates="repositories")
