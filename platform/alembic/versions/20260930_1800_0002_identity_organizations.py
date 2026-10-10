"""Identité et organisations : users, sessions, staff_roles, organizations, memberships

Revision ID: 0002_identity
Revises: 0001_core
Create Date: 2026-09-30 18:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_identity"
down_revision: str | None = "0001_core"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TIMESTAMPTZ = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.execute("""
        CREATE FUNCTION app_current_user_id() RETURNS uuid
        LANGUAGE sql STABLE AS
        $$ SELECT NULLIF(current_setting('app.current_user_id', true), '')::uuid $$
        """)

    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("full_name", sa.String(200), nullable=False),
        sa.Column("phone", sa.String(32), nullable=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("email_verified_at", TIMESTAMPTZ, nullable=True),
        sa.Column("last_login_at", TIMESTAMPTZ, nullable=True),
        sa.Column("created_at", TIMESTAMPTZ, server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", TIMESTAMPTZ, server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", TIMESTAMPTZ, nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
    )
    op.create_index("uq_users_email_lower", "users", [sa.text("lower(email)")], unique=True)

    op.create_table(
        "sessions",
        sa.Column("id", sa.String(64), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", TIMESTAMPTZ, server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", TIMESTAMPTZ, nullable=False),
        sa.Column("absolute_expires_at", TIMESTAMPTZ, nullable=False),
        sa.Column("revoked_at", TIMESTAMPTZ, nullable=True),
        sa.Column("ip", sa.String(45), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_sessions_user_id_users", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_sessions"),
    )
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])
    op.create_index("ix_sessions_expires_at", "sessions", ["expires_at"])

    op.create_table(
        "staff_roles",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("granted_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", TIMESTAMPTZ, server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "role IN ('ADMIN', 'MANAGER', 'CONTENT_MANAGER', 'DEVELOPER', 'FINANCE')",
            name="ck_staff_roles_role_valid",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_staff_roles_user_id_users", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("user_id", "role", name="pk_staff_roles"),
    )

    op.create_table(
        "organizations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("commercial_name", sa.String(200), nullable=False),
        sa.Column("legal_name", sa.String(200), nullable=True),
        sa.Column("sector", sa.String(50), nullable=True),
        sa.Column("sub_sector", sa.String(100), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("country", sa.String(2), nullable=False),
        sa.Column("city", sa.String(100), nullable=True),
        sa.Column("address", sa.String(300), nullable=True),
        sa.Column("phone", sa.String(32), nullable=True),
        sa.Column("whatsapp", sa.String(32), nullable=True),
        sa.Column("email", sa.String(320), nullable=True),
        sa.Column("website", sa.String(300), nullable=True),
        sa.Column("primary_color", sa.String(7), nullable=True),
        sa.Column("secondary_color", sa.String(7), nullable=True),
        sa.Column("business_hours", postgresql.JSONB(), nullable=True),
        sa.Column("status", sa.String(16), server_default="LEAD", nullable=False),
        sa.Column("created_at", TIMESTAMPTZ, server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", TIMESTAMPTZ, server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", TIMESTAMPTZ, nullable=True),
        sa.CheckConstraint(
            "status IN ('LEAD', 'ACTIVE', 'SUSPENDED', 'CHURNED')",
            name="ck_organizations_status_valid",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_organizations"),
    )
    op.create_index("ix_organizations_status", "organizations", ["status"])
    op.create_index("ix_organizations_sector", "organizations", ["sector"])

    op.create_table(
        "memberships",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), server_default="ACTIVE", nullable=False),
        sa.Column("created_at", TIMESTAMPTZ, server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", TIMESTAMPTZ, server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "role IN ('CLIENT_OWNER', 'CLIENT_MEMBER')", name="ck_memberships_role_valid"
        ),
        sa.CheckConstraint("status IN ('ACTIVE', 'REVOKED')", name="ck_memberships_status_valid"),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_memberships_organization_id_organizations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_memberships_user_id_users", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_memberships"),
        sa.UniqueConstraint("organization_id", "user_id", name="uq_memberships_organization_user"),
    )
    op.create_index("ix_memberships_user_id", "memberships", ["user_id"])

    # ── RLS ──
    # Appartenances : visibles par l'organisation courante, le staff, et l'utilisateur
    # concerné (pour lister SES organisations). Création réservée au contexte de l'organisation.
    op.execute("ALTER TABLE memberships ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE memberships FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY membership_access ON memberships
        USING (
            app_is_staff()
            OR organization_id = app_current_org_id()
            OR user_id = app_current_user_id()
        )
        WITH CHECK (app_is_staff() OR organization_id = app_current_org_id())
        """)
    # Organisations : visibles par le staff, dans leur propre contexte, ou par leurs membres
    op.execute("ALTER TABLE organizations ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE organizations FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY organization_access ON organizations
        USING (
            app_is_staff()
            OR id = app_current_org_id()
            OR id IN (
                SELECT organization_id FROM memberships
                WHERE user_id = app_current_user_id() AND status = 'ACTIVE'
            )
        )
        WITH CHECK (app_is_staff() OR id = app_current_org_id())
        """)


def downgrade() -> None:
    # La politique des organisations lit memberships : elle doit disparaître avant la table
    op.execute("DROP POLICY organization_access ON organizations")
    op.drop_table("memberships")
    op.drop_table("organizations")
    op.drop_table("staff_roles")
    op.drop_table("sessions")
    op.drop_index("uq_users_email_lower", table_name="users")
    op.drop_table("users")
    op.execute("DROP FUNCTION app_current_user_id()")
