import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from digital360.core.db import Base
from digital360.core.ids import uuid7


class EntitlementOverride(Base):
    """Droit accordé à la main par l'équipe BENILAB (geste commercial, test, compensation)."""

    __tablename__ = "entitlement_overrides"
    __table_args__ = (Index("ix_entitlement_overrides_organization_id", "organization_id"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE")
    )
    entitlement_key: Mapped[str] = mapped_column(String(64))
    # true/false, un entier ou "UNLIMITED" : validé contre le catalogue à l'écriture
    value: Mapped[Any] = mapped_column(JSONB)
    reason: Mapped[str] = mapped_column(Text)
    granted_by: Mapped[uuid.UUID] = mapped_column(Uuid)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
