"""Closer 3.0 : closers, clients apportés, commissions et relevés mensuels.

Ces tables n'appartiennent à aucune organisation (un closer est une personne). Leur RLS
n'ouvre l'accès qu'à la portée staff : le service y accède toujours par une transaction
staff en filtrant explicitement sur l'utilisateur connecté.
"""

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
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from digital360.core.db import Base
from digital360.core.ids import uuid7


class CloserStatus(StrEnum):
    ACTIVE = "ACTIVE"
    # Suspendu par l'équipe : plus de nouveaux clients ni de nouvelles commissions
    SUSPENDED = "SUSPENDED"


class PayoutMethod(StrEnum):
    ORANGE_MONEY = "ORANGE_MONEY"
    MTN_MOMO = "MTN_MOMO"
    MOOV_MONEY = "MOOV_MONEY"
    WAVE = "WAVE"
    BANK_TRANSFER = "BANK_TRANSFER"


class ReferralSource(StrEnum):
    # Lien ou code du closer saisi à l'inscription
    CODE = "CODE"
    # Rattaché à la main par l'équipe (client amené par téléphone…)
    MANUAL = "MANUAL"


class StatementStatus(StrEnum):
    DUE = "DUE"
    PAID = "PAID"


PAYOUT_METHODS_SQL = "'ORANGE_MONEY', 'MTN_MOMO', 'MOOV_MONEY', 'WAVE', 'BANK_TRANSFER'"


class Closer(Base):
    __tablename__ = "closers"
    __table_args__ = (
        CheckConstraint("status IN ('ACTIVE', 'SUSPENDED')", name="status_valid"),
        CheckConstraint(f"payout_method IN ({PAYOUT_METHODS_SQL})", name="payout_method_valid"),
        Index("uq_closers_user_id", "user_id", unique=True),
        Index("uq_closers_code", "code", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"))
    # Code de parrainage partagé (lien ?ref=CODE)
    code: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), server_default=CloserStatus.ACTIVE)
    # Où verser les commissions : numéro mobile money ou coordonnées bancaires
    payout_method: Mapped[str] = mapped_column(String(16))
    payout_account: Mapped[str] = mapped_column(String(64))
    terms_accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Referral(Base):
    """Client apporté par un closer : une entreprise n'a qu'un closer, attribué à l'inscription."""

    __tablename__ = "closer_referrals"
    __table_args__ = (
        CheckConstraint("source IN ('CODE', 'MANUAL')", name="source_valid"),
        Index("ix_closer_referrals_closer_id", "closer_id"),
        Index("uq_closer_referrals_organization_id", "organization_id", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)
    closer_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("closers.id", ondelete="CASCADE"))
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE")
    )
    source: Mapped[str] = mapped_column(String(8))
    # Membre de l'équipe pour un rattachement manuel
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CommissionStatement(Base):
    """Relevé mensuel d'un closer, dans une devise : à verser, puis versé par l'équipe."""

    __tablename__ = "commission_statements"
    __table_args__ = (
        CheckConstraint("status IN ('DUE', 'PAID')", name="status_valid"),
        Index(
            "uq_commission_statements_closer_period_currency",
            "closer_id",
            "period",
            "currency",
            unique=True,
        ),
        Index("ix_commission_statements_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)
    closer_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("closers.id", ondelete="CASCADE"))
    # Premier jour du mois couvert
    period: Mapped[date] = mapped_column(Date)
    currency: Mapped[str] = mapped_column(String(3))
    total_amount: Mapped[int] = mapped_column(Integer)
    commission_count: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(8), server_default=StatementStatus.DUE)
    payout_method: Mapped[str | None] = mapped_column(String(16))
    payout_reference: Mapped[str | None] = mapped_column(String(100))
    paid_on: Mapped[date | None] = mapped_column(Date)
    paid_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Commission(Base):
    """20 % d'un paiement encaissé d'un client apporté (un paiement ne rapporte qu'une fois)."""

    __tablename__ = "commissions"
    __table_args__ = (
        CheckConstraint("amount >= 0", name="amount_positive"),
        Index("ix_commissions_closer_id", "closer_id"),
        Index("ix_commissions_statement_id", "statement_id"),
        Index("uq_commissions_payment_id", "payment_id", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)
    closer_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("closers.id", ondelete="CASCADE"))
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE")
    )
    payment_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("payments.id", ondelete="CASCADE")
    )
    product_code: Mapped[str] = mapped_column(String(50))
    # Montant HT encaissé, taux (points de base : 2000 = 20 %) et commission, en unité mineure
    base_amount: Mapped[int] = mapped_column(Integer)
    rate_bps: Mapped[int] = mapped_column(Integer)
    amount: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3))
    earned_on: Mapped[date] = mapped_column(Date)
    statement_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("commission_statements.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
