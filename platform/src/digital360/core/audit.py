"""Journal d'audit append-only (ARCHITECTURE.md §14.3).

Une ligne écrite ne peut plus être modifiée ni supprimée : un trigger PostgreSQL le refuse.
"""

import re
import uuid
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Index, String, Text, Uuid, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from digital360.core.actor import Actor
from digital360.core.db import Base
from digital360.core.ids import uuid7
from digital360.core.logging import request_id_var

REDACTED = "***"

_SENSITIVE_KEY = re.compile(
    r"pass(word)?|secret|token|api[_-]?key|authorization|cookie|otp|card", re.IGNORECASE
)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_organization_id_occurred_at", "organization_id", "occurred_at"),
        Index("ix_audit_logs_entity", "entity_type", "entity_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    actor_type: Mapped[str] = mapped_column(String(16))
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    actor_label: Mapped[str | None] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(100))
    entity_type: Mapped[str] = mapped_column(String(100))
    entity_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    old_value: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    new_value: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    request_id: Mapped[str | None] = mapped_column(String(128))
    ip: Mapped[str | None] = mapped_column(String(45))
    user_agent: Mapped[str | None] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


def redact(value: Any) -> Any:
    """Masque récursivement les valeurs dont la clé est sensible (mots de passe, jetons…)."""
    if isinstance(value, Mapping):
        return {
            key: REDACTED if _SENSITIVE_KEY.search(str(key)) else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list | tuple):
        return [redact(item) for item in value]
    return value


async def record_audit(
    session: AsyncSession,
    *,
    actor: Actor,
    action: str,
    entity_type: str,
    entity_id: uuid.UUID | None = None,
    organization_id: uuid.UUID | None = None,
    old_value: Mapping[str, Any] | None = None,
    new_value: Mapping[str, Any] | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
) -> AuditLog:
    """Ajoute une entrée d'audit dans la transaction courante (écrite seulement si elle réussit)."""
    entry = AuditLog(
        organization_id=organization_id,
        actor_type=actor.type.value,
        actor_user_id=actor.user_id,
        actor_label=actor.label,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        old_value=redact(old_value) if old_value is not None else None,
        new_value=redact(new_value) if new_value is not None else None,
        request_id=request_id_var.get(),
        ip=ip,
        user_agent=user_agent,
    )
    session.add(entry)
    await session.flush()
    return entry


async def list_audit_logs(
    session: AsyncSession,
    *,
    limit: int,
    after: uuid.UUID | None,
    organization_id: uuid.UUID | None = None,
    action: str | None = None,
    entity_type: str | None = None,
) -> list[AuditLog]:
    """Entrées du plus récent au plus ancien ; renvoie `limit + 1` lignes pour la pagination."""
    statement = select(AuditLog).order_by(AuditLog.id.desc()).limit(limit + 1)
    if after is not None:
        statement = statement.where(AuditLog.id < after)
    if organization_id is not None:
        statement = statement.where(AuditLog.organization_id == organization_id)
    if action:
        statement = statement.where(AuditLog.action == action)
    if entity_type:
        statement = statement.where(AuditLog.entity_type == entity_type)
    return list((await session.execute(statement)).scalars())
