import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from digital360.core.db import Base
from digital360.core.ids import uuid7


class PurchaseRequestStatus(StrEnum):
    NEW = "NEW"
    CONTACTED = "CONTACTED"
    WON = "WON"
    LOST = "LOST"


# Une demande « ouverte » bloque une nouvelle demande pour la même offre
OPEN_STATUSES = (PurchaseRequestStatus.NEW, PurchaseRequestStatus.CONTACTED)


class ContactChannel(StrEnum):
    WHATSAPP = "WHATSAPP"
    PHONE = "PHONE"
    EMAIL = "EMAIL"


class PurchaseRequest(Base):
    """« Je veux démarrer » : demande d'achat traitée à la main par l'équipe, en attendant
    le paiement en ligne (M5). Le prix est figé au moment de la demande."""

    __tablename__ = "purchase_requests"
    __table_args__ = (
        CheckConstraint("status IN ('NEW', 'CONTACTED', 'WON', 'LOST')", name="status_valid"),
        CheckConstraint("channel IN ('WHATSAPP', 'PHONE', 'EMAIL')", name="channel_valid"),
        Index("ix_purchase_requests_organization_id", "organization_id"),
        Index("ix_purchase_requests_status", "status"),
        # Pas deux demandes en cours pour la même offre (protège aussi des doubles clics)
        Index(
            "uq_purchase_requests_open_product",
            "organization_id",
            "product_code",
            unique=True,
            postgresql_where=text("status IN ('NEW', 'CONTACTED')"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE")
    )
    requested_by: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"))
    product_code: Mapped[str] = mapped_column(String(50))
    plan_item_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    # Prix HT au moment de la demande, en unité mineure (comme le catalogue)
    amount: Mapped[int | None] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3))
    period: Mapped[str | None] = mapped_column(String(8))
    channel: Mapped[str] = mapped_column(String(16))
    # Numéro à rappeler (WhatsApp ou téléphone), figé à la demande ; vide pour EMAIL
    contact_number: Mapped[str | None] = mapped_column(String(32))
    message: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), server_default=PurchaseRequestStatus.NEW)
    staff_note: Mapped[str | None] = mapped_column(Text)
    handled_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
