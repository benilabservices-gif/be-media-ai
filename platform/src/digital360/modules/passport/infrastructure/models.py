import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, UniqueConstraint, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from digital360.core.db import Base
from digital360.core.ids import uuid7


class PassportItemStatus(StrEnum):
    NOT_CONFIGURED = "NOT_CONFIGURED"
    IN_PROGRESS = "IN_PROGRESS"
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    ERROR = "ERROR"
    EXPIRED = "EXPIRED"


class PassportSource(StrEnum):
    # Déclaré par le client au diagnostic : jamais présenté comme un fait vérifié
    DECLARED = "DECLARED"
    VERIFIED = "VERIFIED"
    SYNCED = "SYNCED"


class DigitalPassport(Base):
    __tablename__ = "digital_passports"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), unique=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class PassportItem(Base):
    __tablename__ = "passport_items"
    __table_args__ = (
        UniqueConstraint("passport_id", "item_key", name="uq_passport_items_passport_item"),
        CheckConstraint(
            "status IN ('NOT_CONFIGURED', 'IN_PROGRESS', 'ACTIVE', 'PAUSED', 'ERROR', 'EXPIRED')",
            name="status_valid",
        ),
        CheckConstraint("source IN ('DECLARED', 'VERIFIED', 'SYNCED')", name="source_valid"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)
    passport_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("digital_passports.id", ondelete="CASCADE")
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    item_key: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16))
    source: Mapped[str] = mapped_column(String(16))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    status_changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
