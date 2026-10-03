"""Catalogue : type de configuration CATALOG et droits accordés manuellement

Revision ID: 0004_catalog
Revises: 0003_diagnostics
Create Date: 2026-09-30 20:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from digital360.core.tenancy import tenant_rls_statements

revision: str = "0004_catalog"
down_revision: str | None = "0003_diagnostics"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KIND_CONSTRAINT = "ck_config_documents_kind_valid"


def upgrade() -> None:
    op.drop_constraint(KIND_CONSTRAINT, "config_documents", type_="check")
    op.create_check_constraint(
        KIND_CONSTRAINT,
        "config_documents",
        "kind IN ('QUESTIONNAIRE', 'SCORING_MODEL', 'RULE_SET', 'CATALOG')",
    )

    op.create_table(
        "entitlement_overrides",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("entitlement_key", sa.String(64), nullable=False),
        sa.Column("value", postgresql.JSONB(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("granted_by", sa.Uuid(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_entitlement_overrides_organization_id_organizations",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_entitlement_overrides"),
    )
    op.create_index(
        "ix_entitlement_overrides_organization_id", "entitlement_overrides", ["organization_id"]
    )
    for statement in tenant_rls_statements("entitlement_overrides"):
        op.execute(statement)


def downgrade() -> None:
    op.drop_table("entitlement_overrides")
    op.execute("DELETE FROM config_documents WHERE kind = 'CATALOG'")
    op.drop_constraint(KIND_CONSTRAINT, "config_documents", type_="check")
    op.create_check_constraint(
        KIND_CONSTRAINT,
        "config_documents",
        "kind IN ('QUESTIONNAIRE', 'SCORING_MODEL', 'RULE_SET')",
    )
