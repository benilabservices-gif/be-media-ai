import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text, Uuid, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from digital360.core.db import Base
from digital360.core.ids import uuid7


class OrganizationStatus(StrEnum):
    # Créée (souvent après un diagnostic) mais aucun achat encore
    LEAD = "LEAD"
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    CHURNED = "CHURNED"


class Organization(Base):
    """Entreprise cliente : racine du tenant (RLS sur `id`)."""

    __tablename__ = "organizations"
    __table_args__ = (
        CheckConstraint(
            "status IN ('LEAD', 'ACTIVE', 'SUSPENDED', 'CHURNED')", name="status_valid"
        ),
        Index("ix_organizations_status", "status"),
        Index("ix_organizations_sector", "sector"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)
    commercial_name: Mapped[str] = mapped_column(String(200))
    legal_name: Mapped[str | None] = mapped_column(String(200))
    sector: Mapped[str | None] = mapped_column(String(50))
    sub_sector: Mapped[str | None] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(Text)
    country: Mapped[str] = mapped_column(String(2))
    city: Mapped[str | None] = mapped_column(String(100))
    address: Mapped[str | None] = mapped_column(String(300))
    phone: Mapped[str | None] = mapped_column(String(32))
    whatsapp: Mapped[str | None] = mapped_column(String(32))
    email: Mapped[str | None] = mapped_column(String(320))
    website: Mapped[str | None] = mapped_column(String(300))
    primary_color: Mapped[str | None] = mapped_column(String(7))
    secondary_color: Mapped[str | None] = mapped_column(String(7))
    business_hours: Mapped[dict[str, str] | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(16), server_default=OrganizationStatus.LEAD.value)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class InvitationStatus(StrEnum):
    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"
    CANCELLED = "CANCELLED"


class OrganizationInvitation(Base):
    """Invitation à rejoindre l'équipe d'une entreprise, acceptée par lien e-mail.

    Le jeton n'est jamais stocké en clair : la tâche d'envoi le génère et n'en garde que
    l'empreinte. Accepter exige d'être connecté avec l'adresse invitée.
    """

    __tablename__ = "organization_invitations"
    __table_args__ = (
        CheckConstraint("status IN ('PENDING', 'ACCEPTED', 'CANCELLED')", name="status_valid"),
        CheckConstraint("role IN ('CLIENT_OWNER', 'CLIENT_MEMBER')", name="role_valid"),
        Index("ix_organization_invitations_organization_id", "organization_id"),
        Index("uq_organization_invitations_token_hash", "token_hash", unique=True),
        # Une seule invitation en attente par adresse et par entreprise
        Index(
            "uq_organization_invitations_pending_email",
            "organization_id",
            "email",
            unique=True,
            postgresql_where=text("status = 'PENDING'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE")
    )
    # Toujours en minuscules
    email: Mapped[str] = mapped_column(String(320))
    role: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), server_default=InvitationStatus.PENDING.value)
    token_hash: Mapped[str | None] = mapped_column(String(64))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    invited_by: Mapped[uuid.UUID] = mapped_column(Uuid)
    accepted_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
