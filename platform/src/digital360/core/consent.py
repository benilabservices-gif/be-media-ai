"""Consentements RGPD, en ajout seul (ARCHITECTURE.md §14.2)."""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import Boolean, CheckConstraint, DateTime, Index, String, Uuid, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from digital360.core.db import Base
from digital360.core.ids import uuid7

# Version de la politique de confidentialité acceptée : à incrémenter à chaque modification
PRIVACY_POLICY_VERSION = "2026-09"


class ConsentPurpose(StrEnum):
    PRIVACY = "PRIVACY"
    MARKETING_EMAIL = "MARKETING_EMAIL"
    MARKETING_WHATSAPP = "MARKETING_WHATSAPP"


class ConsentRecord(Base):
    __tablename__ = "consent_records"
    __table_args__ = (
        CheckConstraint(
            "subject_type IN ('USER', 'DIAGNOSTIC_SESSION')", name="subject_type_valid"
        ),
        Index("ix_consent_records_subject", "subject_type", "subject_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)
    subject_type: Mapped[str] = mapped_column(String(32))
    subject_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    purpose: Mapped[str] = mapped_column(String(32))
    policy_version: Mapped[str] = mapped_column(String(20))
    granted: Mapped[bool] = mapped_column(Boolean)
    source: Mapped[str] = mapped_column(String(50))
    ip: Mapped[str | None] = mapped_column(String(45))
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


async def record_consents(
    session: AsyncSession,
    *,
    subject_type: str,
    subject_id: uuid.UUID,
    choices: dict[ConsentPurpose, bool],
    source: str,
    ip: str | None,
) -> None:
    for purpose, granted in choices.items():
        session.add(
            ConsentRecord(
                subject_type=subject_type,
                subject_id=subject_id,
                purpose=purpose.value,
                policy_version=PRIVACY_POLICY_VERSION,
                granted=granted,
                source=source,
                ip=ip,
            )
        )
    await session.flush()
