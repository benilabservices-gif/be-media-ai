"""Paiement en ligne SasPay : moyen de paiement SASPAY (CARTFLOX gardé pour l'historique)

Revision ID: 0016_saspay_payments
Revises: 0015_performance
Create Date: 2026-10-16 20:00:00+00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0016_saspay_payments"
down_revision: str | None = "0015_performance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MANUAL_METHODS = "'ORANGE_MONEY', 'MTN_MOMO', 'MOOV_MONEY', 'WAVE', 'BANK_TRANSFER', 'CASH'"


def upgrade() -> None:
    op.drop_constraint("ck_payments_method_valid", "payments", type_="check")
    op.create_check_constraint(
        "ck_payments_method_valid",
        "payments",
        f"method IN ({MANUAL_METHODS}, 'SASPAY', 'CARTFLOX')",
    )


def downgrade() -> None:
    # Échoue si des paiements SasPay existent déjà : on ne les efface pas
    op.drop_constraint("ck_payments_method_valid", "payments", type_="check")
    op.create_check_constraint(
        "ck_payments_method_valid", "payments", f"method IN ({MANUAL_METHODS}, 'CARTFLOX')"
    )
