"""User model — an authenticated GitHub user, optionally belonging to a workspace."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, Boolean, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from slopolis_db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from slopolis_db.models.workspace import Workspace


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A GitHub account that signed in; a workspace member once one is joined.

    ``workspace_id`` is nullable on purpose: sign-in creates the account, and the
    user then either creates a workspace (becoming its admin) or waits for an
    invitation (spec 10.1, onboarding).
    """

    __tablename__ = "users"

    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    github_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True)
    handle: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    avatar_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    workspace: Mapped[Workspace | None] = relationship(back_populates="users")
