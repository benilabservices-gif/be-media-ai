import uuid

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from digital360.core.actor import Actor
from digital360.core.audit import REDACTED, AuditLog, record_audit
from digital360.core.logging import request_id_var
from digital360.core.tenancy import staff_transaction

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


async def _create_entry(factory: async_sessionmaker[AsyncSession]) -> uuid.UUID:
    token = request_id_var.set("req-audit-1")
    try:
        async with staff_transaction(factory) as session:
            entry = await record_audit(
                session,
                actor=Actor.user(uuid.uuid4()),
                action="organization.update",
                entity_type="organization",
                entity_id=uuid.uuid4(),
                organization_id=uuid.uuid4(),
                old_value={"name": "Avant", "api_key": "sk_live_123"},
                new_value={"name": "Après", "nested": {"password": "hunter2"}},
            )
            return entry.id
    finally:
        request_id_var.reset(token)


async def test_should_persist_entry_with_redacted_secrets_and_request_id(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    entry_id = await _create_entry(session_factory)

    async with staff_transaction(session_factory) as session:
        entry = (
            await session.execute(select(AuditLog).where(AuditLog.id == entry_id))
        ).scalar_one()

    assert entry.old_value == {"name": "Avant", "api_key": REDACTED}
    assert entry.new_value == {"name": "Après", "nested": {"password": REDACTED}}
    assert entry.request_id == "req-audit-1"
    assert entry.actor_type == "USER"


async def test_should_forbid_updating_an_audit_entry(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    entry_id = await _create_entry(session_factory)

    with pytest.raises(DBAPIError, match="ajout seul"):
        async with staff_transaction(session_factory) as session:
            await session.execute(
                update(AuditLog).where(AuditLog.id == entry_id).values(action="falsifie")
            )


async def test_should_forbid_deleting_an_audit_entry(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    entry_id = await _create_entry(session_factory)

    with pytest.raises(DBAPIError, match="ajout seul"):
        async with staff_transaction(session_factory) as session:
            await session.execute(delete(AuditLog).where(AuditLog.id == entry_id))
