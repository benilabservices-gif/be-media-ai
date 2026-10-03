import uuid
from datetime import date, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from digital360.core.db import Base
from digital360.core.ids import uuid7


class ProjectStatus(StrEnum):
    BRIEF_PENDING = "BRIEF_PENDING"  # offre activée, le client remplit son brief
    IN_PRODUCTION = "IN_PRODUCTION"  # brief complet et périmètre accepté : l'équipe produit
    CLIENT_REVIEW = "CLIENT_REVIEW"  # première version en ligne, le client relit
    REVISION = "REVISION"  # le client a demandé ses corrections de contenu
    APPROVED = "APPROVED"  # le client a validé
    LIVE = "LIVE"  # site en ligne sur son domaine
    CANCELLED = "CANCELLED"


STATUS_VALUES = "', '".join(status.value for status in ProjectStatus)


class WebsiteProject(Base):
    """Réalisation d'un site Digital Start, créée quand la demande d'achat est gagnée."""

    __tablename__ = "website_projects"
    __table_args__ = (
        CheckConstraint(f"status IN ('{STATUS_VALUES}')", name="status_valid"),
        Index("ix_website_projects_organization_id", "organization_id"),
        Index("ix_website_projects_status", "status"),
        # Une vente gagnée ne crée qu'un projet, même si la tâche est rejouée
        Index(
            "uq_website_projects_purchase_request_id",
            "purchase_request_id",
            unique=True,
            postgresql_where=text("purchase_request_id IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE")
    )
    purchase_request_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    product_code: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(32), server_default=ProjectStatus.BRIEF_PENDING)
    brief: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    scope_accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scope_accepted_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    brief_submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    due_on: Mapped[date | None] = mapped_column(Date)
    preview_url: Mapped[str | None] = mapped_column(String(500))
    live_url: Mapped[str | None] = mapped_column(String(500))
    revisions_used: Mapped[int] = mapped_column(Integer, server_default="0")
    # Historique des demandes de corrections : [{submitted_at, items: [{category, page, text}]}]
    revision_requests: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
