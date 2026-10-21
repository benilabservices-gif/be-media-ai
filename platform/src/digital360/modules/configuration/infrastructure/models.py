import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from digital360.core.db import Base
from digital360.core.ids import uuid7


class ConfigKind(StrEnum):
    QUESTIONNAIRE = "QUESTIONNAIRE"
    SCORING_MODEL = "SCORING_MODEL"
    RULE_SET = "RULE_SET"
    CATALOG = "CATALOG"


class ConfigDocument(Base):
    """Version immuable d'une configuration métier."""

    __tablename__ = "config_documents"
    __table_args__ = (
        UniqueConstraint("kind", "key", "version", name="uq_config_documents_kind_key_version"),
        Index(
            "uq_config_documents_published",
            "kind",
            "key",
            unique=True,
            postgresql_where=text("status = 'PUBLISHED'"),
        ),
        CheckConstraint(
            "kind IN ('QUESTIONNAIRE', 'SCORING_MODEL', 'RULE_SET', 'CATALOG')", name="kind_valid"
        ),
        CheckConstraint("status IN ('PUBLISHED', 'ARCHIVED')", name="status_valid"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)
    kind: Mapped[str] = mapped_column(String(32))
    key: Mapped[str] = mapped_column(String(50))
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16))
    definition: Mapped[dict[str, Any]] = mapped_column(JSONB)
    checksum: Mapped[str] = mapped_column(String(64))
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
