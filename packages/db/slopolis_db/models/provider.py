"""Provider credentials, model catalog, and role→model assignments."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    LargeBinary,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from slopolis_db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from slopolis_db.models.workspace import Workspace


class ProviderCredential(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """An encrypted BYOK provider credential in the workspace vault."""

    __tablename__ = "provider_credentials"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    provider: Mapped[str] = mapped_column(String(100), nullable=False)
    base_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    encrypted_api_key: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    key_last4: Mapped[str] = mapped_column(String(4), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    last_checked_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    workspace: Mapped[Workspace] = relationship(back_populates="credentials")
    models: Mapped[list[ModelCatalog]] = relationship(
        back_populates="credential",
        cascade="all, delete-orphan",
    )


class ModelCatalog(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A model known to the workspace, imported from a provider or added manually."""

    __tablename__ = "model_catalog"
    __table_args__ = (UniqueConstraint("workspace_id", "model_id"),)

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    credential_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("provider_credentials.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    model_id: Mapped[str] = mapped_column(String(255), nullable=False)
    provider: Mapped[str] = mapped_column(String(100), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source: Mapped[str] = mapped_column(String(50), nullable=False)

    workspace: Mapped[Workspace] = relationship(back_populates="model_catalog")
    credential: Mapped[ProviderCredential | None] = relationship(back_populates="models")


class ModelAssignment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Workspace-level mapping from an agent role to a model."""

    __tablename__ = "model_assignments"
    __table_args__ = (UniqueConstraint("workspace_id", "role"),)

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[str] = mapped_column(String(100), nullable=False)
    model_catalog_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("model_catalog.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    model_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    workspace: Mapped[Workspace] = relationship(back_populates="model_assignments")
