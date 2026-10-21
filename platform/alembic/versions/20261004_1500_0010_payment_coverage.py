"""Paiements d'abonnement : période couverte et rappel d'échéance

Revision ID: 0010_payment_coverage
Revises: 0009_payments
Create Date: 2026-10-04 15:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_payment_coverage"
down_revision: str | None = "0009_payments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("payments", sa.Column("covers_until", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "payments", sa.Column("reminder_sent_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_index("ix_payments_covers_until", "payments", ["covers_until"])


def downgrade() -> None:
    op.drop_index("ix_payments_covers_until", table_name="payments")
    op.drop_column("payments", "reminder_sent_at")
    op.drop_column("payments", "covers_until")
