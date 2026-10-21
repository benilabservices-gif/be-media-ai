"""Paiement en ligne Cartflox : online_checkouts et moyen de paiement CARTFLOX

Revision ID: 0011_online_checkouts
Revises: 0010_payment_coverage
Create Date: 2026-10-04 18:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from digital360.core.tenancy import tenant_rls_statements

revision: str = "0011_online_checkouts"
down_revision: str | None = "0010_payment_coverage"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MANUAL_METHODS = "'ORANGE_MONEY', 'MTN_MOMO', 'MOOV_MONEY', 'WAVE', 'BANK_TRANSFER', 'CASH'"


def upgrade() -> None:
    op.drop_constraint("ck_payments_method_valid", "payments", type_="check")
    op.create_check_constraint(
        "ck_payments_method_valid", "payments", f"method IN ({MANUAL_METHODS}, 'CARTFLOX')"
    )

    op.create_table(
        "online_checkouts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("requested_by", sa.Uuid(), nullable=False),
        sa.Column("product_code", sa.String(50), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("period", sa.String(8), nullable=True),
        sa.Column("status", sa.String(16), server_default="PENDING", nullable=False),
        sa.Column("provider_session_id", sa.String(100), nullable=True),
        sa.Column("provider_order_id", sa.String(100), nullable=True),
        sa.Column("checkout_url", sa.Text(), nullable=True),
        sa.Column("provider", sa.String(50), nullable=True),
        sa.Column("provider_reference", sa.String(100), nullable=True),
        sa.Column("payment_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('PENDING', 'PAID', 'FAILED', 'CANCELLED', 'EXPIRED')",
            name="ck_online_checkouts_status_valid",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_online_checkouts_organization_id_organizations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by"], ["users.id"], name="fk_online_checkouts_requested_by_users"
        ),
        sa.ForeignKeyConstraint(
            ["payment_id"],
            ["payments.id"],
            name="fk_online_checkouts_payment_id_payments",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_online_checkouts"),
    )
    op.create_index("ix_online_checkouts_organization_id", "online_checkouts", ["organization_id"])
    op.create_index(
        "uq_online_checkouts_provider_session_id",
        "online_checkouts",
        ["provider_session_id"],
        unique=True,
    )
    for statement in tenant_rls_statements("online_checkouts"):
        op.execute(statement)


def downgrade() -> None:
    op.drop_table("online_checkouts")
    op.drop_constraint("ck_payments_method_valid", "payments", type_="check")
    op.create_check_constraint(
        "ck_payments_method_valid", "payments", f"method IN ({MANUAL_METHODS})"
    )
