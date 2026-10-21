"""Diagnostic, score, plan d'action, passport, configuration versionnée, consentements

Revision ID: 0003_diagnostics
Revises: 0002_identity
Create Date: 2026-09-30 19:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from digital360.core.tenancy import tenant_rls_statements

revision: str = "0003_diagnostics"
down_revision: str | None = "0002_identity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TIMESTAMPTZ = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "config_documents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("key", sa.String(50), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("definition", postgresql.JSONB(), nullable=False),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("published_at", TIMESTAMPTZ, server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "kind IN ('QUESTIONNAIRE', 'SCORING_MODEL', 'RULE_SET')",
            name="ck_config_documents_kind_valid",
        ),
        sa.CheckConstraint(
            "status IN ('PUBLISHED', 'ARCHIVED')", name="ck_config_documents_status_valid"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_config_documents"),
        sa.UniqueConstraint("kind", "key", "version", name="uq_config_documents_kind_key_version"),
    )
    op.create_index(
        "uq_config_documents_published",
        "config_documents",
        ["kind", "key"],
        unique=True,
        postgresql_where=sa.text("status = 'PUBLISHED'"),
    )

    op.create_table(
        "consent_records",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("subject_type", sa.String(32), nullable=False),
        sa.Column("subject_id", sa.Uuid(), nullable=False),
        sa.Column("purpose", sa.String(32), nullable=False),
        sa.Column("policy_version", sa.String(20), nullable=False),
        sa.Column("granted", sa.Boolean(), nullable=False),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("ip", sa.String(45), nullable=True),
        sa.Column("occurred_at", TIMESTAMPTZ, server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "subject_type IN ('USER', 'DIAGNOSTIC_SESSION')",
            name="ck_consent_records_subject_type_valid",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_consent_records"),
    )
    op.create_index("ix_consent_records_subject", "consent_records", ["subject_type", "subject_id"])
    op.execute(
        "CREATE TRIGGER consent_records_append_only BEFORE UPDATE OR DELETE ON consent_records "
        "FOR EACH ROW EXECUTE FUNCTION app_prevent_mutation()"
    )

    op.create_table(
        "diagnostic_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=True),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("questionnaire_id", sa.Uuid(), nullable=False),
        sa.Column("scoring_model_id", sa.Uuid(), nullable=False),
        sa.Column("rule_set_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(16), server_default="IN_PROGRESS", nullable=False),
        sa.Column(
            "answers", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column("facts", postgresql.JSONB(), nullable=True),
        sa.Column("source", postgresql.JSONB(), nullable=True),
        sa.Column("marketing_email_consent", sa.Boolean(), server_default="false", nullable=False),
        sa.Column(
            "marketing_whatsapp_consent", sa.Boolean(), server_default="false", nullable=False
        ),
        sa.Column("completed_at", TIMESTAMPTZ, nullable=True),
        sa.Column("claimed_at", TIMESTAMPTZ, nullable=True),
        sa.Column("created_at", TIMESTAMPTZ, server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", TIMESTAMPTZ, server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "status IN ('IN_PROGRESS', 'COMPLETED', 'CLAIMED')",
            name="ck_diagnostic_sessions_status_valid",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_diagnostic_sessions_organization_id_organizations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["questionnaire_id"],
            ["config_documents.id"],
            name="fk_diagnostic_sessions_questionnaire_id_config_documents",
        ),
        sa.ForeignKeyConstraint(
            ["scoring_model_id"],
            ["config_documents.id"],
            name="fk_diagnostic_sessions_scoring_model_id_config_documents",
        ),
        sa.ForeignKeyConstraint(
            ["rule_set_id"],
            ["config_documents.id"],
            name="fk_diagnostic_sessions_rule_set_id_config_documents",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_diagnostic_sessions"),
        sa.UniqueConstraint("token_hash", name="uq_diagnostic_sessions_token_hash"),
    )
    op.create_index(
        "ix_diagnostic_sessions_organization_id", "diagnostic_sessions", ["organization_id"]
    )
    op.create_index(
        "ix_diagnostic_sessions_status_created_at",
        "diagnostic_sessions",
        ["status", "created_at"],
    )

    op.create_table(
        "digital_scores",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=True),
        sa.Column("scoring_model_id", sa.Uuid(), nullable=False),
        sa.Column("total", sa.Integer(), nullable=False),
        sa.Column("categories", postgresql.JSONB(), nullable=False),
        sa.Column("maturity_level", sa.String(32), nullable=False),
        sa.Column("maturity_label", sa.String(100), nullable=False),
        sa.Column("computed_at", TIMESTAMPTZ, server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["diagnostic_sessions.id"],
            name="fk_digital_scores_session_id_diagnostic_sessions",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["scoring_model_id"],
            ["config_documents.id"],
            name="fk_digital_scores_scoring_model_id_config_documents",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_digital_scores"),
        sa.UniqueConstraint(
            "session_id", "scoring_model_id", name="uq_digital_scores_session_model"
        ),
    )
    op.create_index("ix_digital_scores_organization_id", "digital_scores", ["organization_id"])

    op.create_table(
        "action_plans",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=True),
        sa.Column("rule_set_id", sa.Uuid(), nullable=False),
        sa.Column("generated_at", TIMESTAMPTZ, server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["diagnostic_sessions.id"],
            name="fk_action_plans_session_id_diagnostic_sessions",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["rule_set_id"],
            ["config_documents.id"],
            name="fk_action_plans_rule_set_id_config_documents",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_action_plans"),
        sa.UniqueConstraint("session_id", name="uq_action_plans_session_id"),
    )
    op.create_index("ix_action_plans_organization_id", "action_plans", ["organization_id"])

    op.create_table(
        "action_plan_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("plan_id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("rule_key", sa.String(100), nullable=False),
        sa.Column("module", sa.String(32), nullable=False),
        sa.Column("phase", sa.String(16), nullable=False),
        sa.Column("priority", sa.String(16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("current_state", sa.Text(), nullable=False),
        sa.Column("recommended_action", sa.Text(), nullable=False),
        sa.Column("product_code", sa.String(50), nullable=True),
        sa.Column("cta", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), server_default="PROPOSED", nullable=False),
        sa.Column("created_at", TIMESTAMPTZ, server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", TIMESTAMPTZ, server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "status IN ('PROPOSED', 'ACCEPTED', 'IN_PROGRESS', 'DONE', 'DISMISSED')",
            name="ck_action_plan_items_status_valid",
        ),
        sa.CheckConstraint(
            "priority IN ('CRITICAL', 'HIGH', 'MEDIUM', 'LOW')",
            name="ck_action_plan_items_priority_valid",
        ),
        sa.ForeignKeyConstraint(
            ["plan_id"],
            ["action_plans.id"],
            name="fk_action_plan_items_plan_id_action_plans",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_action_plan_items"),
    )
    op.create_index(
        "ix_action_plan_items_plan_id_position", "action_plan_items", ["plan_id", "position"]
    )

    op.create_table(
        "digital_passports",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", TIMESTAMPTZ, server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", TIMESTAMPTZ, server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_digital_passports_organization_id_organizations",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_digital_passports"),
        sa.UniqueConstraint("organization_id", name="uq_digital_passports_organization_id"),
    )

    op.create_table(
        "passport_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("passport_id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("item_key", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("details", postgresql.JSONB(), server_default="{}", nullable=False),
        sa.Column("status_changed_at", TIMESTAMPTZ, server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", TIMESTAMPTZ, server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "status IN ('NOT_CONFIGURED', 'IN_PROGRESS', 'ACTIVE', 'PAUSED', 'ERROR', 'EXPIRED')",
            name="ck_passport_items_status_valid",
        ),
        sa.CheckConstraint(
            "source IN ('DECLARED', 'VERIFIED', 'SYNCED')", name="ck_passport_items_source_valid"
        ),
        sa.ForeignKeyConstraint(
            ["passport_id"],
            ["digital_passports.id"],
            name="fk_passport_items_passport_id_digital_passports",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_passport_items"),
        sa.UniqueConstraint("passport_id", "item_key", name="uq_passport_items_passport_item"),
    )

    # ── RLS ──
    # Diagnostic : l'organisation qui l'a rattaché, le staff, ou le porteur du jeton anonyme
    op.execute("""
        CREATE FUNCTION app_diagnostic_token_hash() RETURNS text
        LANGUAGE sql STABLE AS
        $$ SELECT NULLIF(current_setting('app.diagnostic_token_hash', true), '') $$
        """)
    op.execute("ALTER TABLE diagnostic_sessions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE diagnostic_sessions FORCE ROW LEVEL SECURITY")
    predicate = (
        "app_is_staff() OR organization_id = app_current_org_id() "
        "OR token_hash = app_diagnostic_token_hash()"
    )
    op.execute(
        f"CREATE POLICY diagnostic_access ON diagnostic_sessions "
        f"USING ({predicate}) WITH CHECK ({predicate})"
    )
    # Score, plan, recommandations : visibles si le diagnostic parent l'est
    for table, parent in (
        ("digital_scores", "session_id IN (SELECT id FROM diagnostic_sessions)"),
        ("action_plans", "session_id IN (SELECT id FROM diagnostic_sessions)"),
        ("action_plan_items", "plan_id IN (SELECT id FROM action_plans)"),
    ):
        child_predicate = f"app_is_staff() OR organization_id = app_current_org_id() OR {parent}"
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY diagnostic_child_access ON {table} "
            f"USING ({child_predicate}) WITH CHECK ({child_predicate})"
        )
    for table in ("digital_passports", "passport_items"):
        for statement in tenant_rls_statements(table):
            op.execute(statement)


def downgrade() -> None:
    op.drop_table("passport_items")
    op.drop_table("digital_passports")
    op.drop_table("action_plan_items")
    op.drop_table("action_plans")
    op.drop_table("digital_scores")
    op.drop_table("diagnostic_sessions")
    op.execute("DROP FUNCTION app_diagnostic_token_hash()")
    op.drop_table("consent_records")
    op.drop_table("config_documents")
