"""Projets de site Digital Start : website_projects

Revision ID: 0008_website_projects
Revises: 0007_purchase_request_contact
Create Date: 2026-10-04 09:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from digital360.core.tenancy import tenant_rls_statements

revision: str = "0008_website_projects"
down_revision: str | None = "0007_purchase_request_contact"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TIMESTAMPTZ = sa.DateTime(timezone=True)
STATUSES = (
    "'BRIEF_PENDING', 'IN_PRODUCTION', 'CLIENT_REVIEW', 'REVISION', 'APPROVED', 'LIVE', "
    "'CANCELLED'"
)


def upgrade() -> None:
    op.create_table(
        "website_projects",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("purchase_request_id", sa.Uuid(), nullable=True),
        sa.Column("product_code", sa.String(50), nullable=False),
        sa.Column("status", sa.String(32), server_default="BRIEF_PENDING", nullable=False),
        sa.Column(
            "brief", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column("scope_accepted_at", TIMESTAMPTZ, nullable=True),
        sa.Column("scope_accepted_by", sa.Uuid(), nullable=True),
        sa.Column("brief_submitted_at", TIMESTAMPTZ, nullable=True),
        sa.Column("due_on", sa.Date(), nullable=True),
        sa.Column("preview_url", sa.String(500), nullable=True),
        sa.Column("live_url", sa.String(500), nullable=True),
        sa.Column("revisions_used", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "revision_requests",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("created_at", TIMESTAMPTZ, server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", TIMESTAMPTZ, server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(f"status IN ({STATUSES})", name="ck_website_projects_status_valid"),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_website_projects_organization_id_organizations",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_website_projects"),
    )
    op.create_index("ix_website_projects_organization_id", "website_projects", ["organization_id"])
    op.create_index("ix_website_projects_status", "website_projects", ["status"])
    op.create_index(
        "uq_website_projects_purchase_request_id",
        "website_projects",
        ["purchase_request_id"],
        unique=True,
        postgresql_where=sa.text("purchase_request_id IS NOT NULL"),
    )
    for statement in tenant_rls_statements("website_projects"):
        op.execute(statement)


def downgrade() -> None:
    op.drop_table("website_projects")
