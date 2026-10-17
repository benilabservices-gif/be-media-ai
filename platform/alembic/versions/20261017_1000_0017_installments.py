"""Paiement en plusieurs fois : solde dû, rappels et suspension d'une vente

Revision ID: 0017_installments
Revises: 0016_saspay_payments
Create Date: 2026-10-17 10:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017_installments"
down_revision: str | None = "0016_saspay_payments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("purchase_requests", sa.Column("balance_due_on", sa.Date(), nullable=True))
    op.add_column(
        "purchase_requests",
        sa.Column(
            "balance_reminder_stage", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
    )
    op.add_column(
        "purchase_requests",
        sa.Column("suspended_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Contrôle quotidien des soldes : seules les ventes avec un solde dû sont lues
    op.create_index(
        "ix_purchase_requests_balance_due_on",
        "purchase_requests",
        ["balance_due_on"],
        postgresql_where=sa.text("balance_due_on IS NOT NULL"),
    )
    # Une vente peut désormais recevoir plusieurs paiements (acompte puis solde)
    op.drop_index("uq_payments_purchase_request_id", table_name="payments")
    op.create_index(
        "ix_payments_purchase_request_id",
        "payments",
        ["purchase_request_id"],
        postgresql_where=sa.text("purchase_request_id IS NOT NULL"),
    )


def downgrade() -> None:
    # Échoue si une vente a déjà plusieurs paiements : on ne les efface pas
    op.drop_index("ix_payments_purchase_request_id", table_name="payments")
    op.create_index(
        "uq_payments_purchase_request_id",
        "payments",
        ["purchase_request_id"],
        unique=True,
        postgresql_where=sa.text("purchase_request_id IS NOT NULL"),
    )
    op.drop_index("ix_purchase_requests_balance_due_on", table_name="purchase_requests")
    op.drop_column("purchase_requests", "suspended_at")
    op.drop_column("purchase_requests", "balance_reminder_stage")
    op.drop_column("purchase_requests", "balance_due_on")
