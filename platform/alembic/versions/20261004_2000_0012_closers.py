"""Closer 3.0 : closers, clients apportés, commissions et relevés mensuels

Revision ID: 0012_closers
Revises: 0011_online_checkouts
Create Date: 2026-10-04 20:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_closers"
down_revision: str | None = "0011_online_checkouts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PAYOUT_METHODS = "'ORANGE_MONEY', 'MTN_MOMO', 'MOOV_MONEY', 'WAVE', 'BANK_TRANSFER'"
TABLES = ("closers", "closer_referrals", "commission_statements", "commissions")


def _timestamp(name: str) -> sa.Column:  # type: ignore[type-arg]
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def upgrade() -> None:
    op.create_table(
        "closers",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), server_default="ACTIVE", nullable=False),
        sa.Column("payout_method", sa.String(16), nullable=False),
        sa.Column("payout_account", sa.String(64), nullable=False),
        sa.Column("terms_accepted_at", sa.DateTime(timezone=True), nullable=False),
        _timestamp("created_at"),
        _timestamp("updated_at"),
        sa.CheckConstraint("status IN ('ACTIVE', 'SUSPENDED')", name="ck_closers_status_valid"),
        sa.CheckConstraint(
            f"payout_method IN ({PAYOUT_METHODS})", name="ck_closers_payout_method_valid"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_closers_user_id_users", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_closers"),
    )
    op.create_index("uq_closers_user_id", "closers", ["user_id"], unique=True)
    op.create_index("uq_closers_code", "closers", ["code"], unique=True)

    op.create_table(
        "closer_referrals",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("closer_id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("source", sa.String(8), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        _timestamp("created_at"),
        sa.CheckConstraint("source IN ('CODE', 'MANUAL')", name="ck_closer_referrals_source_valid"),
        sa.ForeignKeyConstraint(
            ["closer_id"],
            ["closers.id"],
            name="fk_closer_referrals_closer_id_closers",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_closer_referrals_organization_id_organizations",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_closer_referrals"),
    )
    op.create_index("ix_closer_referrals_closer_id", "closer_referrals", ["closer_id"])
    op.create_index(
        "uq_closer_referrals_organization_id",
        "closer_referrals",
        ["organization_id"],
        unique=True,
    )

    op.create_table(
        "commission_statements",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("closer_id", sa.Uuid(), nullable=False),
        sa.Column("period", sa.Date(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("total_amount", sa.Integer(), nullable=False),
        sa.Column("commission_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(8), server_default="DUE", nullable=False),
        sa.Column("payout_method", sa.String(16), nullable=True),
        sa.Column("payout_reference", sa.String(100), nullable=True),
        sa.Column("paid_on", sa.Date(), nullable=True),
        sa.Column("paid_by", sa.Uuid(), nullable=True),
        _timestamp("created_at"),
        _timestamp("updated_at"),
        sa.CheckConstraint(
            "status IN ('DUE', 'PAID')", name="ck_commission_statements_status_valid"
        ),
        sa.ForeignKeyConstraint(
            ["closer_id"],
            ["closers.id"],
            name="fk_commission_statements_closer_id_closers",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_commission_statements"),
    )
    op.create_index(
        "uq_commission_statements_closer_period_currency",
        "commission_statements",
        ["closer_id", "period", "currency"],
        unique=True,
    )
    op.create_index("ix_commission_statements_status", "commission_statements", ["status"])

    op.create_table(
        "commissions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("closer_id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("payment_id", sa.Uuid(), nullable=False),
        sa.Column("product_code", sa.String(50), nullable=False),
        sa.Column("base_amount", sa.Integer(), nullable=False),
        sa.Column("rate_bps", sa.Integer(), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("earned_on", sa.Date(), nullable=False),
        sa.Column("statement_id", sa.Uuid(), nullable=True),
        _timestamp("created_at"),
        sa.CheckConstraint("amount >= 0", name="ck_commissions_amount_positive"),
        sa.ForeignKeyConstraint(
            ["closer_id"],
            ["closers.id"],
            name="fk_commissions_closer_id_closers",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_commissions_organization_id_organizations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["payment_id"],
            ["payments.id"],
            name="fk_commissions_payment_id_payments",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["statement_id"],
            ["commission_statements.id"],
            name="fk_commissions_statement_id_commission_statements",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_commissions"),
    )
    op.create_index("ix_commissions_closer_id", "commissions", ["closer_id"])
    op.create_index("ix_commissions_statement_id", "commissions", ["statement_id"])
    op.create_index("uq_commissions_payment_id", "commissions", ["payment_id"], unique=True)

    # Pas d'organisation propriétaire : accès réservé à la portée staff (le service filtre
    # toujours sur l'utilisateur connecté) ; sans portée, aucune ligne n'est visible
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY staff_only ON {table} "
            "USING (app_is_staff()) WITH CHECK (app_is_staff())"
        )


def downgrade() -> None:
    for table in reversed(TABLES):
        op.drop_table(table)
