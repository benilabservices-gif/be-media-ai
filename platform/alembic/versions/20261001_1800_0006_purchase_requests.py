"""Demandes d'achat « Je veux démarrer » : purchase_requests

Revision ID: 0006_purchase_requests
Revises: 0005_password_reset
Create Date: 2026-10-01 18:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from digital360.core.tenancy import tenant_rls_statements

revision: str = "0006_purchase_requests"
down_revision: str | None = "0005_password_reset"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TIMESTAMPTZ = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "purchase_requests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("requested_by", sa.Uuid(), nullable=False),
        sa.Column("product_code", sa.String(50), nullable=False),
        sa.Column("plan_item_id", sa.Uuid(), nullable=True),
        sa.Column("amount", sa.Integer(), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("period", sa.String(8), nullable=True),
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("status", sa.String(16), server_default="NEW", nullable=False),
        sa.Column("staff_note", sa.Text(), nullable=True),
        sa.Column("handled_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", TIMESTAMPTZ, server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", TIMESTAMPTZ, server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "status IN ('NEW', 'CONTACTED', 'WON', 'LOST')",
            name="ck_purchase_requests_status_valid",
        ),
        sa.CheckConstraint(
            "channel IN ('WHATSAPP', 'PHONE', 'EMAIL')",
            name="ck_purchase_requests_channel_valid",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_purchase_requests_organization_id_organizations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by"], ["users.id"], name="fk_purchase_requests_requested_by_users"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_purchase_requests"),
    )
    op.create_index(
        "ix_purchase_requests_organization_id", "purchase_requests", ["organization_id"]
    )
    op.create_index("ix_purchase_requests_status", "purchase_requests", ["status"])
    op.create_index(
        "uq_purchase_requests_open_product",
        "purchase_requests",
        ["organization_id", "product_code"],
        unique=True,
        postgresql_where=sa.text("status IN ('NEW', 'CONTACTED')"),
    )
    for statement in tenant_rls_statements("purchase_requests"):
        op.execute(statement)


def downgrade() -> None:
    op.drop_table("purchase_requests")
