"""Isolation des tenants par PostgreSQL (RLS), testée sur la table workflow_transitions."""

import uuid

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from digital360.core.db import role_bypasses_rls
from digital360.core.tenancy import TenantContext, staff_transaction, tenant_transaction
from digital360.core.workflow import WorkflowTransition

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


def _transition(organization_id: uuid.UUID) -> WorkflowTransition:
    return WorkflowTransition(
        organization_id=organization_id,
        entity_type="test",
        entity_id=uuid.uuid4(),
        from_status="A",
        to_status="B",
        actor_type="SYSTEM",
    )


async def _seed(factory: async_sessionmaker[AsyncSession]) -> tuple[uuid.UUID, uuid.UUID]:
    org_a, org_b = uuid.uuid4(), uuid.uuid4()
    async with staff_transaction(factory) as session:
        session.add_all([_transition(org_a), _transition(org_a), _transition(org_b)])
    return org_a, org_b


async def _count(session: AsyncSession, organization_id: uuid.UUID) -> int:
    return (
        await session.scalar(
            select(func.count())
            .select_from(WorkflowTransition)
            .where(WorkflowTransition.organization_id == organization_id)
        )
        or 0
    )


async def test_should_connect_with_role_subject_to_rls(engine: AsyncEngine) -> None:
    assert await role_bypasses_rls(engine) is False


async def test_should_only_see_own_organization_rows_in_tenant_scope(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    org_a, org_b = await _seed(session_factory)

    async with tenant_transaction(session_factory, TenantContext(org_a)) as session:
        # Même en demandant explicitement l'organisation B, la base ne renvoie rien
        assert await _count(session, org_a) == 2
        assert await _count(session, org_b) == 0


async def test_should_see_no_row_without_tenant_context(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    org_a, _ = await _seed(session_factory)

    async with session_factory() as session, session.begin():
        assert await _count(session, org_a) == 0


async def test_should_see_all_organizations_in_staff_scope(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    org_a, org_b = await _seed(session_factory)

    async with staff_transaction(session_factory) as session:
        assert await _count(session, org_a) == 2
        assert await _count(session, org_b) == 1


async def test_should_reject_insert_for_another_organization_in_tenant_scope(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    org_a, org_b = uuid.uuid4(), uuid.uuid4()

    with pytest.raises(DBAPIError, match="row-level security"):
        async with tenant_transaction(session_factory, TenantContext(org_a)) as session:
            session.add(_transition(org_b))
            await session.flush()


async def test_should_not_leak_tenant_context_to_next_transaction(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    org_a, _ = await _seed(session_factory)

    async with session_factory() as session:
        async with session.begin():
            await session.execute(
                text("SELECT set_config('app.current_org_id', :org, true)"), {"org": str(org_a)}
            )
            assert await _count(session, org_a) == 2
        # Nouvelle transaction sur la même session (et souvent la même connexion du pool)
        async with session.begin():
            assert await _count(session, org_a) == 0
