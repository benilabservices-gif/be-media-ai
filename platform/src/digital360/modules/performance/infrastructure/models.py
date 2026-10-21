import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from digital360.core.db import Base
from digital360.core.ids import uuid7


class GoogleBusinessSetup(Base):
    """Avancement de la mise en place de la fiche Google Business d'une entreprise."""

    __tablename__ = "google_business_setups"
    __table_args__ = (
        CheckConstraint(
            "status IN ('NOT_STARTED', 'PROFILE_CREATED', 'ACCESS_GRANTED', 'VERIFIED', 'ACTIVE')",
            name="status_valid",
        ),
        Index("uq_google_business_setups_organization_id", "organization_id", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE")
    )
    status: Mapped[str] = mapped_column(String(32), server_default="NOT_STARTED")
    # Lien public de la fiche (Google Maps), affiché au client une fois créée
    profile_url: Mapped[str | None] = mapped_column(String(500))
    # Message de l'équipe au client (prochaine action attendue, par exemple)
    note: Mapped[str | None] = mapped_column(Text)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class MonthlyReport(Base):
    """Chiffres d'un mois pour un canal (Google Business ou site web), saisis par l'équipe."""

    __tablename__ = "monthly_reports"
    __table_args__ = (
        CheckConstraint("channel IN ('GOOGLE_BUSINESS', 'WEBSITE')", name="channel_valid"),
        Index(
            "uq_monthly_reports_organization_channel_period",
            "organization_id",
            "channel",
            "period",
            unique=True,
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE")
    )
    channel: Mapped[str] = mapped_column(String(32))
    # Premier jour du mois couvert
    period: Mapped[date] = mapped_column(Date)
    # {clé d'indicateur: nombre} ; une clé absente = non mesurée ce mois-ci
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    # Recherches qui ont mené à la fiche Google (5 au plus)
    top_searches: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    # Commentaire de l'équipe : ce qui a été fait, ce qui est prévu
    note: Mapped[str | None] = mapped_column(Text)
    entered_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
