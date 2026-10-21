"""Infrastructure transverse : tenancy (RLS), audit, transitions, jobs, idempotence

Revision ID: 0001_core
Revises:
Create Date: 2026-09-30 17:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from digital360.core.tenancy import tenant_rls_statements

revision: str = "0001_core"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TIMESTAMPTZ = sa.DateTime(timezone=True)


def upgrade() -> None:
    # ── Fonctions de contexte tenant, lues par les politiques RLS ──
    # current_setting(..., true) renvoie NULL si la variable n'est pas posée : aucune ligne visible
    op.execute("""
        CREATE FUNCTION app_current_org_id() RETURNS uuid
        LANGUAGE sql STABLE AS
        $$ SELECT NULLIF(current_setting('app.current_org_id', true), '')::uuid $$
        """)
    op.execute("""
        CREATE FUNCTION app_is_staff() RETURNS boolean
        LANGUAGE sql STABLE AS
        $$ SELECT coalesce(current_setting('app.scope', true), '') = 'staff' $$
        """)
    op.execute("""
        CREATE FUNCTION app_prevent_mutation() RETURNS trigger
        LANGUAGE plpgsql AS
        $$ BEGIN
            RAISE EXCEPTION 'la table % est en ajout seul : % interdit', TG_TABLE_NAME, TG_OP
                USING ERRCODE = 'insufficient_privilege';
        END $$
        """)

    # ── Journal d'audit (append-only) ──
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=True),
        sa.Column("actor_type", sa.String(16), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("actor_label", sa.String(100), nullable=True),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("entity_type", sa.String(100), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=True),
        sa.Column("old_value", postgresql.JSONB(), nullable=True),
        sa.Column("new_value", postgresql.JSONB(), nullable=True),
        sa.Column("request_id", sa.String(128), nullable=True),
        sa.Column("ip", sa.String(45), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("occurred_at", TIMESTAMPTZ, server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_audit_logs"),
    )
    op.create_index(
        "ix_audit_logs_organization_id_occurred_at",
        "audit_logs",
        ["organization_id", "occurred_at"],
    )
    op.create_index("ix_audit_logs_entity", "audit_logs", ["entity_type", "entity_id"])
    op.execute(
        "CREATE TRIGGER audit_logs_append_only BEFORE UPDATE OR DELETE ON audit_logs "
        "FOR EACH ROW EXECUTE FUNCTION app_prevent_mutation()"
    )

    # ── Historique des transitions de workflow (tenant, append-only) ──
    op.create_table(
        "workflow_transitions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("entity_type", sa.String(100), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("from_status", sa.String(50), nullable=False),
        sa.Column("to_status", sa.String(50), nullable=False),
        sa.Column("actor_type", sa.String(16), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("actor_label", sa.String(100), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("details", postgresql.JSONB(), nullable=True),
        sa.Column("occurred_at", TIMESTAMPTZ, server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_workflow_transitions"),
    )
    op.create_index(
        "ix_workflow_transitions_entity",
        "workflow_transitions",
        ["entity_type", "entity_id", "occurred_at"],
    )
    op.create_index(
        "ix_workflow_transitions_organization_id", "workflow_transitions", ["organization_id"]
    )
    op.execute(
        "CREATE TRIGGER workflow_transitions_append_only BEFORE UPDATE OR DELETE "
        "ON workflow_transitions FOR EACH ROW EXECUTE FUNCTION app_prevent_mutation()"
    )
    for statement in tenant_rls_statements("workflow_transitions"):
        op.execute(statement)

    # ── File de tâches / outbox ──
    op.create_table(
        "jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(16), server_default="PENDING", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("run_at", TIMESTAMPTZ, server_default=sa.func.now(), nullable=False),
        sa.Column("locked_at", TIMESTAMPTZ, nullable=True),
        sa.Column("locked_by", sa.String(100), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("dedup_key", sa.String(300), nullable=True),
        sa.Column("organization_id", sa.Uuid(), nullable=True),
        sa.Column("request_id", sa.String(128), nullable=True),
        sa.Column("created_at", TIMESTAMPTZ, server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", TIMESTAMPTZ, nullable=True),
        sa.CheckConstraint(
            "status IN ('PENDING', 'RUNNING', 'SUCCEEDED', 'DEAD')",
            name="ck_jobs_status_valid",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_jobs"),
    )
    op.create_index(
        "ix_jobs_pending_run_at",
        "jobs",
        ["run_at"],
        postgresql_where=sa.text("status = 'PENDING'"),
    )
    op.create_index(
        "uq_jobs_dedup_key",
        "jobs",
        ["dedup_key"],
        unique=True,
        postgresql_where=sa.text("dedup_key IS NOT NULL"),
    )

    # ── Clés d'idempotence ──
    op.create_table(
        "idempotency_keys",
        sa.Column("scope", sa.String(300), nullable=False),
        sa.Column("key", sa.String(255), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("response_status", sa.Integer(), nullable=True),
        sa.Column("response_body", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", TIMESTAMPTZ, server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", TIMESTAMPTZ, nullable=True),
        sa.CheckConstraint(
            "status IN ('IN_PROGRESS', 'COMPLETED')", name="ck_idempotency_keys_status_valid"
        ),
        sa.PrimaryKeyConstraint("scope", "key", name="pk_idempotency_keys"),
    )


def downgrade() -> None:
    op.drop_table("idempotency_keys")
    op.drop_table("jobs")
    op.drop_table("workflow_transitions")
    op.drop_table("audit_logs")
    op.execute("DROP FUNCTION app_prevent_mutation()")
    op.execute("DROP FUNCTION app_is_staff()")
    op.execute("DROP FUNCTION app_current_org_id()")
