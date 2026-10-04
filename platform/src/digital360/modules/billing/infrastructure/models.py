import uuid
from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    Date,
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


class PaymentMethod(StrEnum):
    ORANGE_MONEY = "ORANGE_MONEY"
    MTN_MOMO = "MTN_MOMO"
    MOOV_MONEY = "MOOV_MONEY"
    WAVE = "WAVE"
    BANK_TRANSFER = "BANK_TRANSFER"
    CASH = "CASH"


class PaymentChannel(StrEnum):
    # Saisi par l'équipe après un encaissement hors plateforme
    MANUAL = "MANUAL"
    # Confirmé par le fournisseur de paiement en ligne (M5)
    ONLINE = "ONLINE"


class Payment(Base):
    """Encaissement d'une vente. Manuel aujourd'hui, en ligne demain : même table, même suite
    (activation de l'offre, reçu, chiffre d'affaires)."""

    __tablename__ = "payments"
    __table_args__ = (
        CheckConstraint(
            "method IN ('ORANGE_MONEY', 'MTN_MOMO', 'MOOV_MONEY', 'WAVE', 'BANK_TRANSFER', 'CASH')",
            name="method_valid",
        ),
        CheckConstraint("channel IN ('MANUAL', 'ONLINE')", name="channel_valid"),
        CheckConstraint("amount >= 0", name="amount_positive"),
        Index("ix_payments_organization_id", "organization_id"),
        Index("ix_payments_covers_until", "covers_until"),
        # Une vente n'est encaissée qu'une fois
        Index(
            "uq_payments_purchase_request_id",
            "purchase_request_id",
            unique=True,
            postgresql_where=text("purchase_request_id IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE")
    )
    purchase_request_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("purchase_requests.id", ondelete="SET NULL")
    )
    product_code: Mapped[str] = mapped_column(String(50))
    # Montant réellement reçu, HT, en unité mineure (peut différer du prix : geste commercial)
    amount: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3))
    method: Mapped[str] = mapped_column(String(16))
    channel: Mapped[str] = mapped_column(String(8))
    # Identifiant de la transaction (mobile money, virement) : preuve en cas de litige
    reference: Mapped[str | None] = mapped_column(String(100))
    received_on: Mapped[date] = mapped_column(Date)
    recorded_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    # Abonnement : fin de la période payée (vide pour une offre ponctuelle comme Start)
    covers_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Rappel « échéance proche » déjà envoyé pour cette période
    reminder_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
