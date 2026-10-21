"""Paiements (manuels aujourd'hui, en ligne demain) : payments

Revision ID: 0009_payments
Revises: 0008_website_projects
Create Date: 2026-10-04 12:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from digital360.core.tenancy import tenant_rls_statements

revision: str = "0009_payments"
down_revision: str | None = "0008_website_projects"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "payments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("purchase_request_id", sa.Uuid(), nullable=True),
        sa.Column("product_code", sa.String(50), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("method", sa.String(16), nullable=False),
        sa.Column("channel", sa.String(8), nullable=False),
        sa.Column("reference", sa.String(100), nullable=True),
        sa.Column("received_on", sa.Date(), nullable=False),
        sa.Column("recorded_by", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "method IN ('ORANGE_MONEY', 'MTN_MOMO', 'MOOV_MONEY', 'WAVE', 'BANK_TRANSFER', 'CASH')",
            name="ck_payments_method_valid",
        ),
        sa.CheckConstraint("channel IN ('MANUAL', 'ONLINE')", name="ck_payments_channel_valid"),
        sa.CheckConstraint("amount >= 0", name="ck_payments_amount_positive"),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_payments_organization_id_organizations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["purchase_request_id"],
            ["purchase_requests.id"],
            name="fk_payments_purchase_request_id_purchase_requests",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_payments"),
    )
    op.create_index("ix_payments_organization_id", "payments", ["organization_id"])
    op.create_index(
        "uq_payments_purchase_request_id",
        "payments",
        ["purchase_request_id"],
        unique=True,
        postgresql_where=sa.text("purchase_request_id IS NOT NULL"),
    )
    for statement in tenant_rls_statements("payments"):
        op.execute(statement)


def downgrade() -> None:
    op.drop_table("payments")
