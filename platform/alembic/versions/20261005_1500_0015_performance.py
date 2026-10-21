"""Suivi des résultats : mise en place de Google Business et rapports mensuels

Revision ID: 0015_performance
Revises: 0014_organization_invitations
Create Date: 2026-10-05 15:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from digital360.core.tenancy import tenant_rls_statements

revision: str = "0015_performance"
down_revision: str | None = "0014_organization_invitations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamp(name: str) -> sa.Column:  # type: ignore[type-arg]
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def upgrade() -> None:
    op.create_table(
        "google_business_setups",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(32), server_default="NOT_STARTED", nullable=False),
        sa.Column("profile_url", sa.String(500), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        _timestamp("created_at"),
        _timestamp("updated_at"),
        sa.CheckConstraint(
            "status IN ('NOT_STARTED', 'PROFILE_CREATED', 'ACCESS_GRANTED', 'VERIFIED', 'ACTIVE')",
            name="ck_google_business_setups_status_valid",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_google_business_setups_organization_id_organizations",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_google_business_setups"),
    )
    op.create_index(
        "uq_google_business_setups_organization_id",
        "google_business_setups",
        ["organization_id"],
        unique=True,
    )

    op.create_table(
        "monthly_reports",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("channel", sa.String(32), nullable=False),
        sa.Column("period", sa.Date(), nullable=False),
        sa.Column(
            "metrics",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "top_searches",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("entered_by", sa.Uuid(), nullable=True),
        _timestamp("created_at"),
        _timestamp("updated_at"),
        sa.CheckConstraint(
            "channel IN ('GOOGLE_BUSINESS', 'WEBSITE')", name="ck_monthly_reports_channel_valid"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_monthly_reports_organization_id_organizations",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_monthly_reports"),
    )
    op.create_index(
        "uq_monthly_reports_organization_channel_period",
        "monthly_reports",
        ["organization_id", "channel", "period"],
        unique=True,
    )
    for table in ("google_business_setups", "monthly_reports"):
        for statement in tenant_rls_statements(table):
            op.execute(statement)


def downgrade() -> None:
    op.drop_table("monthly_reports")
    op.drop_table("google_business_setups")
