"""Persistance des transitions de workflow (ARCHITECTURE.md §9.1).

`apply_transition` enchaîne, dans la transaction courante :
vérification (machine à états) → mise à jour avec verrou optimiste → historique →
audit → événement `<machine>.status_changed`.
"""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol

from sqlalchemy import DateTime, Index, String, Text, Uuid, func, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.orm.attributes import set_committed_value

from digital360.core.audit import record_audit
from digital360.core.db import Base
from digital360.core.errors import AppError
from digital360.core.ids import uuid7
from digital360.core.jobs import JobRegistry, publish
from digital360.core.state_machine import StateMachine, TransitionContext


class WorkflowTransition(Base):
    __tablename__ = "workflow_transitions"
    __table_args__ = (
        Index("ix_workflow_transitions_entity", "entity_type", "entity_id", "occurred_at"),
        Index("ix_workflow_transitions_organization_id", "organization_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)
    organization_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    entity_type: Mapped[str] = mapped_column(String(100))
    entity_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    from_status: Mapped[str] = mapped_column(String(50))
    to_status: Mapped[str] = mapped_column(String(50))
    actor_type: Mapped[str] = mapped_column(String(16))
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    actor_label: Mapped[str | None] = mapped_column(String(100))
    reason: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class StatefulEntity(Protocol):
    """Entité ORM pilotée par une machine à états."""

    id: Any
    organization_id: Any
    status: Any


class ConcurrentTransitionError(AppError):
    def __init__(self) -> None:
        super().__init__(
            "CONFLICT",
            "L'élément a été modifié entre-temps. Rechargez la page et réessayez.",
            status=409,
        )


async def apply_transition[S: StrEnum, E: StatefulEntity](
    session: AsyncSession,
    machine: StateMachine[S, E],
    entity: E,
    target: S,
    context: TransitionContext,
    *,
    reason: str | None = None,
    details: dict[str, Any] | None = None,
    registry: JobRegistry | None = None,
) -> WorkflowTransition:
    current = type(target)(entity.status)
    machine.resolve(current, target, entity, context)

    model = type(entity)
    # Verrou optimiste : échoue si une autre requête a changé le statut entre-temps
    result = await session.execute(
        update(model)
        .where(model.id == entity.id, model.status == current.value)
        .values(status=target.value)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:  # type: ignore[attr-defined]
        raise ConcurrentTransitionError
    set_committed_value(entity, "status", target.value)

    entry = WorkflowTransition(
        organization_id=entity.organization_id,
        entity_type=machine.name,
        entity_id=entity.id,
        from_status=current.value,
        to_status=target.value,
        actor_type=context.actor.type.value,
        actor_user_id=context.actor.user_id,
        actor_label=context.actor.label,
        reason=reason,
        details=details,
    )
    session.add(entry)
    await record_audit(
        session,
        actor=context.actor,
        action=f"{machine.name}.transition",
        entity_type=machine.name,
        entity_id=entity.id,
        organization_id=entity.organization_id,
        old_value={"status": current.value},
        new_value={"status": target.value, "reason": reason},
    )
    if registry is not None:
        await publish(
            session,
            registry,
            f"{machine.name}.status_changed",
            {
                "entity_id": str(entity.id),
                "from": current.value,
                "to": target.value,
            },
            organization_id=entity.organization_id,
        )
    return entry
