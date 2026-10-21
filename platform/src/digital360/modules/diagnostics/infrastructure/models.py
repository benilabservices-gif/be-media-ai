import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from digital360.core.db import Base
from digital360.core.ids import uuid7


class DiagnosticStatus(StrEnum):
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    # Rattaché à une organisation après inscription
    CLAIMED = "CLAIMED"


class PlanItemStatus(StrEnum):
    PROPOSED = "PROPOSED"
    ACCEPTED = "ACCEPTED"
    IN_PROGRESS = "IN_PROGRESS"
    DONE = "DONE"
    DISMISSED = "DISMISSED"


class DiagnosticSession(Base):
    """Diagnostic, anonyme (jeton) jusqu'à son rattachement à une organisation."""

    __tablename__ = "diagnostic_sessions"
    __table_args__ = (
        Index("ix_diagnostic_sessions_organization_id", "organization_id"),
        Index("ix_diagnostic_sessions_status_created_at", "status", "created_at"),
        CheckConstraint("status IN ('IN_PROGRESS', 'COMPLETED', 'CLAIMED')", name="status_valid"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE")
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    questionnaire_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("config_documents.id"))
    scoring_model_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("config_documents.id"))
    rule_set_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("config_documents.id"))
    status: Mapped[str] = mapped_column(String(16), server_default=DiagnosticStatus.IN_PROGRESS)
    answers: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    facts: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    source: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    marketing_email_consent: Mapped[bool] = mapped_column(Boolean, server_default="false")
    marketing_whatsapp_consent: Mapped[bool] = mapped_column(Boolean, server_default="false")
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class DigitalScore(Base):
    __tablename__ = "digital_scores"
    __table_args__ = (
        UniqueConstraint("session_id", "scoring_model_id", name="uq_digital_scores_session_model"),
        Index("ix_digital_scores_organization_id", "organization_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("diagnostic_sessions.id", ondelete="CASCADE")
    )
    organization_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    scoring_model_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("config_documents.id"))
    total: Mapped[int] = mapped_column(Integer)
    categories: Mapped[dict[str, Any]] = mapped_column(JSONB)
    maturity_level: Mapped[str] = mapped_column(String(32))
    maturity_label: Mapped[str] = mapped_column(String(100))
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ActionPlan(Base):
    __tablename__ = "action_plans"
    __table_args__ = (Index("ix_action_plans_organization_id", "organization_id"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("diagnostic_sessions.id", ondelete="CASCADE"), unique=True
    )
    organization_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    rule_set_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("config_documents.id"))
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ActionPlanItem(Base):
    __tablename__ = "action_plan_items"
    __table_args__ = (
        Index("ix_action_plan_items_plan_id_position", "plan_id", "position"),
        CheckConstraint(
            "status IN ('PROPOSED', 'ACCEPTED', 'IN_PROGRESS', 'DONE', 'DISMISSED')",
            name="status_valid",
        ),
        CheckConstraint("priority IN ('CRITICAL', 'HIGH', 'MEDIUM', 'LOW')", name="priority_valid"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)
    plan_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("action_plans.id", ondelete="CASCADE")
    )
    organization_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    position: Mapped[int] = mapped_column(Integer)
    rule_key: Mapped[str] = mapped_column(String(100))
    module: Mapped[str] = mapped_column(String(32))
    phase: Mapped[str] = mapped_column(String(16))
    priority: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str] = mapped_column(Text)
    current_state: Mapped[str] = mapped_column(Text)
    recommended_action: Mapped[str] = mapped_column(Text)
    product_code: Mapped[str | None] = mapped_column(String(50))
    cta: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), server_default=PlanItemStatus.PROPOSED)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
