"""Demandes d'achat : numéro à rappeler (contact_number)

Revision ID: 0007_purchase_request_contact
Revises: 0006_purchase_requests
Create Date: 2026-10-03 17:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_purchase_request_contact"
down_revision: str | None = "0006_purchase_requests"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("purchase_requests", sa.Column("contact_number", sa.String(32), nullable=True))


def downgrade() -> None:
    op.drop_column("purchase_requests", "contact_number")
