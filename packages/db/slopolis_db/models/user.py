"""User model — an authenticated GitHub user belonging to a workspace."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, Boolean, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from slopolis_db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from slopolis_db.models.workspace import Workspace


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A workspace member identified by their GitHub account."""

    __tablename__ = "users"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    github_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True)
    handle: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    avatar_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    workspace: Mapped[Workspace] = relationship(back_populates="users")
