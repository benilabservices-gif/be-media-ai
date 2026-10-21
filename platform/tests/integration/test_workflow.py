"""apply_transition sur une table temporaire propre au test (hors schéma applicatif)."""

import uuid
from enum import StrEnum

import pytest
from sqlalchemy import MetaData, String, Uuid, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from digital360.core.actor import Actor
from digital360.core.audit import AuditLog
from digital360.core.jobs import Job, JobRegistry
from digital360.core.state_machine import (
    InvalidTransitionError,
    StateMachine,
    TransitionContext,
    transition,
)
from digital360.core.tenancy import TenantContext, tenant_transaction
from digital360.core.workflow import ConcurrentTransitionError, WorkflowTransition, apply_transition

pytestmark = [
    pytest.mark.integration,
    pytest.mark.anyio,
    pytest.mark.usefixtures("empty_job_queue"),
]


class _TestBase(DeclarativeBase):
    metadata = MetaData()


class Item(_TestBase):
    __tablename__ = "tmp_workflow_items"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    status: Mapped[str] = mapped_column(String(20))


class ItemStatus(StrEnum):
    DRAFT = "DRAFT"
    REVIEW = "REVIEW"
    DONE = "DONE"


MACHINE: StateMachine[ItemStatus, Item] = StateMachine(
    "item",
    [
        transition(ItemStatus.DRAFT, ItemStatus.REVIEW, permissions={"item:edit"}),
        transition(ItemStatus.REVIEW, ItemStatus.DONE, permissions={"item:edit"}),
    ],
)

EDITOR = TransitionContext(Actor.user(uuid.uuid4()), frozenset({"item:edit"}))


async def _create_item(session: AsyncSession, organization_id: uuid.UUID) -> Item:
    await session.execute(
        text(
            "CREATE TEMP TABLE IF NOT EXISTS tmp_workflow_items "
            "(id uuid PRIMARY KEY, organization_id uuid NOT NULL, status varchar(20) NOT NULL) "
            "ON COMMIT DROP"
        )
    )
    item = Item(id=uuid.uuid4(), organization_id=organization_id, status=ItemStatus.DRAFT.value)
    session.add(item)
    await session.flush()
    return item


async def test_should_update_status_and_record_history_audit_and_event(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    organization_id = uuid.uuid4()
    registry = JobRegistry()

    @registry.on("item.status_changed", name="test.on_item_changed")
    async def on_changed(_: AsyncSession, __: dict[str, object]) -> None:
        pass

    async with tenant_transaction(session_factory, TenantContext(organization_id)) as session:
        item = await _create_item(session, organization_id)
        await apply_transition(
            session, MACHINE, item, ItemStatus.REVIEW, EDITOR, reason="prêt", registry=registry
        )

        stored_status = await session.scalar(select(Item.status).where(Item.id == item.id))
        history = (
            await session.execute(
                select(WorkflowTransition).where(WorkflowTransition.entity_id == item.id)
            )
        ).scalar_one()
        audit = (
            await session.execute(select(AuditLog).where(AuditLog.entity_id == item.id))
        ).scalar_one()
        job = (await session.execute(select(Job))).scalar_one()

    assert item.status == stored_status == "REVIEW"
    assert (history.from_status, history.to_status, history.reason) == ("DRAFT", "REVIEW", "prêt")
    assert history.actor_user_id == EDITOR.actor.user_id
    assert audit.action == "item.transition"
    assert job.name == "test.on_item_changed"
    assert job.payload["from"] == "DRAFT" and job.payload["to"] == "REVIEW"


async def test_should_refuse_transition_not_declared_in_machine(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    organization_id = uuid.uuid4()
    with pytest.raises(InvalidTransitionError):
        async with tenant_transaction(session_factory, TenantContext(organization_id)) as session:
            item = await _create_item(session, organization_id)
            await apply_transition(session, MACHINE, item, ItemStatus.DONE, EDITOR)


async def test_should_detect_concurrent_status_change(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    organization_id = uuid.uuid4()
    with pytest.raises(ConcurrentTransitionError):
        async with tenant_transaction(session_factory, TenantContext(organization_id)) as session:
            item = await _create_item(session, organization_id)
            # Une autre requête a déjà fait avancer l'élément, sans que notre objet le sache
            await session.execute(
                text("UPDATE tmp_workflow_items SET status = 'REVIEW' WHERE id = :id"),
                {"id": item.id},
            )
            await apply_transition(session, MACHINE, item, ItemStatus.REVIEW, EDITOR)
