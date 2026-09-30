import uuid
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from digital360.core.actor import Actor
from digital360.core.audit import record_audit
from digital360.core.errors import AppError
from digital360.core.ids import uuid7
from digital360.core.pagination import PageInfo, PageParams, build_page_info
from digital360.core.permissions import ClientRole, Principal
from digital360.core.tenancy import (
    TenantContext,
    staff_transaction,
    tenant_transaction,
    user_transaction,
)
from digital360.modules.identity.application.memberships import (
    MemberView,
    grant_membership,
    list_members,
)
from digital360.modules.organizations.infrastructure.models import (
    Organization,
    OrganizationStatus,
)

AUDITED_FIELDS = (
    "commercial_name",
    "legal_name",
    "sector",
    "sub_sector",
    "description",
    "country",
    "city",
    "address",
    "phone",
    "whatsapp",
    "email",
    "website",
    "primary_color",
    "secondary_color",
    "business_hours",
)


def _not_found() -> AppError:
    return AppError("NOT_FOUND", "Organisation introuvable.", status=404)


def _snapshot(organization: Organization, fields: list[str]) -> dict[str, Any]:
    return {field: getattr(organization, field) for field in fields}


class OrganizationService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create(self, principal: Principal, values: dict[str, Any]) -> Organization:
        """Crée l'organisation ; son créateur en devient propriétaire (CLIENT_OWNER)."""
        organization = Organization(id=uuid7(), **values)
        context = TenantContext(organization_id=organization.id, user_id=principal.user_id)
        async with tenant_transaction(self._session_factory, context) as session:
            session.add(organization)
            await session.flush()
            await grant_membership(
                session,
                organization_id=organization.id,
                user_id=principal.user_id,
                role=ClientRole.CLIENT_OWNER,
            )
            await record_audit(
                session,
                actor=Actor.user(principal.user_id),
                action="organization.create",
                entity_type="organization",
                entity_id=organization.id,
                organization_id=organization.id,
                new_value=_snapshot(organization, list(values)),
            )
            await session.refresh(organization)
        return organization

    async def get(self, context: TenantContext) -> Organization:
        async with tenant_transaction(self._session_factory, context) as session:
            return await self._load(session, context.organization_id)

    async def update(
        self, context: TenantContext, actor: Actor, changes: dict[str, Any]
    ) -> Organization:
        async with tenant_transaction(self._session_factory, context) as session:
            organization = await self._load(session, context.organization_id)
            changed = [
                field for field, value in changes.items() if getattr(organization, field) != value
            ]
            if changed:
                old = _snapshot(organization, changed)
                for field in changed:
                    setattr(organization, field, changes[field])
                await record_audit(
                    session,
                    actor=actor,
                    action="organization.update",
                    entity_type="organization",
                    entity_id=organization.id,
                    organization_id=organization.id,
                    old_value=old,
                    new_value=_snapshot(organization, changed),
                )
                await session.flush()
                await session.refresh(organization)
            return organization

    async def members(self, context: TenantContext) -> list[MemberView]:
        async with tenant_transaction(self._session_factory, context) as session:
            return await list_members(session, context.organization_id)

    async def names_for_user(self, user_id: uuid.UUID) -> dict[uuid.UUID, str]:
        """Noms des organisations dont l'utilisateur est membre (sélecteur d'organisation)."""
        async with user_transaction(self._session_factory, user_id) as session:
            rows = await session.execute(
                select(Organization.id, Organization.commercial_name).where(
                    Organization.deleted_at.is_(None)
                )
            )
            return dict(rows.tuples().all())

    async def admin_list(
        self, params: PageParams, *, status: OrganizationStatus | None, query: str | None
    ) -> tuple[list[Organization], PageInfo]:
        statement = (
            select(Organization)
            .where(Organization.deleted_at.is_(None))
            .order_by(Organization.id.desc())
            .limit(params.limit + 1)
        )
        if params.after is not None:
            statement = statement.where(Organization.id < params.after)
        if status is not None:
            statement = statement.where(Organization.status == status.value)
        if query:
            # % et _ saisis par l'utilisateur sont cherchés littéralement, pas comme jokers
            escaped = query.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
            pattern = f"%{escaped}%"
            statement = statement.where(
                or_(
                    Organization.commercial_name.ilike(pattern),
                    Organization.legal_name.ilike(pattern),
                    Organization.city.ilike(pattern),
                )
            )
        async with staff_transaction(self._session_factory) as session:
            organizations = list((await session.execute(statement)).scalars())
        kept, page = build_page_info([item.id for item in organizations], params)
        return organizations[:kept], page

    async def admin_get(self, organization_id: uuid.UUID) -> Organization:
        async with staff_transaction(self._session_factory) as session:
            return await self._load(session, organization_id)

    async def _load(self, session: AsyncSession, organization_id: uuid.UUID) -> Organization:
        organization = (
            await session.execute(
                select(Organization).where(
                    Organization.id == organization_id, Organization.deleted_at.is_(None)
                )
            )
        ).scalar_one_or_none()
        if organization is None:
            raise _not_found()
        return organization
