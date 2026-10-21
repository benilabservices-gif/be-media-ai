"""Projets de site : prompt de conception (rédigé à l'envoi du brief, amélioré par l'équipe)

Revision ID: 0013_design_prompt
Revises: 0012_closers
Create Date: 2026-10-05 09:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_design_prompt"
down_revision: str | None = "0012_closers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("website_projects", sa.Column("design_prompt", sa.Text(), nullable=True))
    op.add_column(
        "website_projects",
        sa.Column("design_prompt_edited_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "website_projects", sa.Column("design_prompt_edited_by", sa.Uuid(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("website_projects", "design_prompt_edited_by")
    op.drop_column("website_projects", "design_prompt_edited_at")
    op.drop_column("website_projects", "design_prompt")
