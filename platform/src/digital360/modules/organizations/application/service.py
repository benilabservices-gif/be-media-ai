import uuid
from typing import Any

from sqlalchemy import delete, func, or_, select, text
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
from digital360.modules.diagnostics.application.service import (
    attach_to_organization,
    load_claimable,
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
from digital360.modules.passport.application.service import initialize_from_declarations

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


# Réponses du diagnostic reprises comme valeurs par défaut de l'organisation
_ANSWER_TO_FIELD = {
    "company": "commercial_name",
    "country": "country",
    "city": "city",
    "sector": "sector",
    "phone": "phone",
    "whatsapp": "whatsapp",
    "email": "email",
    "description": "description",
}


def _values_from_answers(answers: dict[str, Any]) -> dict[str, Any]:
    values = {
        field: answers[answer] for answer, field in _ANSWER_TO_FIELD.items() if answers.get(answer)
    }
    if "commercial_name" in values:
        values["commercial_name"] = str(values["commercial_name"])[:200]
    if "description" in values:
        values["description"] = str(values["description"])[:2000]
    return values


# Données rattachées à une entreprise, comptées avant sa suppression (tables d'autres
# modules : requêtes paramétrées sur leur nom, sans importer leurs modèles)
_LINKED_TABLES = {
    "members": "SELECT count(*) FROM memberships WHERE organization_id = :id AND status = 'ACTIVE'",
    "diagnostics": "SELECT count(*) FROM diagnostic_sessions WHERE organization_id = :id",
    "payments": "SELECT count(*) FROM payments WHERE organization_id = :id",
    "website_projects": "SELECT count(*) FROM website_projects WHERE organization_id = :id",
    "purchase_requests": "SELECT count(*) FROM purchase_requests WHERE organization_id = :id",
}


async def _linked_counts(session: AsyncSession, organization: Organization) -> dict[str, int]:
    counts = {
        key: int(await session.scalar(text(query), {"id": organization.id}) or 0)
        for key, query in _LINKED_TABLES.items()
    }
    counts["prospects"] = len(await _related_prospects(session, organization))
    return counts


# Prospects (diagnostics) jamais rattachés à un compte mais qui concernent cette entreprise :
# même nom, e-mail de l'entreprise ou d'un membre, ou même numéro (chiffres seuls comparés)
_RELATED_PROSPECTS = text(r"""
    SELECT d.id FROM diagnostic_sessions d
    WHERE d.organization_id IS NULL AND (
        lower(trim(d.answers->>'company')) = lower(trim(:name))
        OR lower(trim(d.answers->>'email')) = ANY(:emails)
        OR regexp_replace(coalesce(d.answers->>'phone', ''), '\D', '', 'g') = ANY(:phones)
        OR regexp_replace(coalesce(d.answers->>'whatsapp', ''), '\D', '', 'g') = ANY(:phones)
    )
    """)


async def _related_prospects(session: AsyncSession, organization: Organization) -> list[uuid.UUID]:
    member_emails = await session.scalars(
        text(
            "SELECT lower(u.email) FROM users u JOIN memberships m ON m.user_id = u.id "
            "WHERE m.organization_id = :id"
        ),
        {"id": organization.id},
    )
    emails = {email for email in member_emails if email}
    if organization.email:
        emails.add(organization.email.strip().lower())
    numbers = (organization.phone, organization.whatsapp)
    phones = {
        digits
        for digits in ("".join(c for c in (number or "") if c.isdigit()) for number in numbers)
        # Un numéro trop court ne suffit pas à rattacher un prospect
        if len(digits) >= 8
    }
    rows = await session.scalars(
        _RELATED_PROSPECTS,
        {"name": organization.commercial_name, "emails": list(emails), "phones": list(phones)},
    )
    return list(rows)


def _not_found() -> AppError:
    return AppError("NOT_FOUND", "Organisation introuvable.", status=404)


def _snapshot(organization: Organization, fields: list[str]) -> dict[str, Any]:
    return {field: getattr(organization, field) for field in fields}


class OrganizationService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create(
        self,
        principal: Principal,
        values: dict[str, Any],
        *,
        diagnostic: tuple[uuid.UUID, str] | None = None,
    ) -> Organization:
        """Crée l'organisation ; son créateur en devient propriétaire (CLIENT_OWNER).

        Avec un diagnostic (identifiant, jeton), les champs non fournis sont repris de ses
        réponses, le diagnostic est rattaché et le Passport initialisé, en une transaction.
        """
        organization_id = uuid7()
        context = TenantContext(organization_id=organization_id, user_id=principal.user_id)
        async with tenant_transaction(self._session_factory, context) as session:
            claimed = None
            if diagnostic is not None:
                claimed = await load_claimable(session, *diagnostic)
                values = _values_from_answers(claimed.answers) | {
                    key: value for key, value in values.items() if value is not None
                }
            missing = [field for field in ("commercial_name", "country") if not values.get(field)]
            if missing:
                raise AppError(
                    "VALIDATION_ERROR",
                    "Nom commercial et pays sont obligatoires.",
                    errors=[{"field": f"body.{field}", "reason": "missing"} for field in missing],
                )
            organization = Organization(id=organization_id, **values)
            session.add(organization)
            await session.flush()
            if claimed is not None:
                await attach_to_organization(session, claimed.session_id, organization_id)
                await initialize_from_declarations(
                    session, organization_id, claimed.passport_statuses
                )
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

    async def admin_stats(self) -> dict[str, int]:
        """Nombre d'entreprises par statut (LEAD, ACTIVE, SUSPENDED, CHURNED)."""
        async with staff_transaction(self._session_factory) as session:
            rows = await session.execute(
                select(Organization.status, func.count())
                .where(Organization.deleted_at.is_(None))
                .group_by(Organization.status)
            )
            counts = dict(rows.tuples().all())
        return {status.value: counts.get(status.value, 0) for status in OrganizationStatus}

    async def admin_deletion_preview(self, organization_id: uuid.UUID) -> dict[str, Any]:
        """Ce qui disparaîtra avec l'entreprise, à montrer avant la confirmation."""
        async with staff_transaction(self._session_factory) as session:
            organization = await self._load(session, organization_id)
            return {
                "organization_id": organization.id,
                "commercial_name": organization.commercial_name,
                **await _linked_counts(session, organization),
            }

    async def admin_delete(
        self,
        organization_id: uuid.UUID,
        staff_user_id: uuid.UUID,
        *,
        confirm_name: str,
        delete_payments: bool,
    ) -> None:
        """Suppression définitive : toutes les données de l'entreprise partent avec elle
        (diagnostics, paiements, projets…). Les comptes des membres sont conservés."""
        async with staff_transaction(self._session_factory) as session:
            organization = (
                await session.execute(
                    select(Organization).where(Organization.id == organization_id).with_for_update()
                )
            ).scalar_one_or_none()
            if organization is None:
                raise _not_found()
            if confirm_name.strip().casefold() != organization.commercial_name.strip().casefold():
                raise AppError(
                    "CONFIRMATION_MISMATCH",
                    "Le nom saisi ne correspond pas au nom de l'entreprise.",
                    status=422,
                    errors=[{"field": "confirm_name", "reason": "mismatch"}],
                )
            counts = await _linked_counts(session, organization)
            if counts["payments"] and not delete_payments:
                raise AppError(
                    "HAS_PAYMENTS",
                    f"Cette entreprise a {counts['payments']} paiement(s) enregistré(s) : confirmez "
                    "explicitement leur suppression.",
                    status=409,
                )
            # Tracé avant la suppression : le journal d'audit n'est pas lié à l'entreprise
            await record_audit(
                session,
                actor=Actor.user(staff_user_id),
                action="organization.delete",
                entity_type="organization",
                entity_id=organization.id,
                organization_id=organization.id,
                old_value={
                    "commercial_name": organization.commercial_name,
                    "country": organization.country,
                    "status": organization.status,
                    **counts,
                },
            )
            # Prospects liés jamais rattachés (ceux de l'entreprise partent en cascade). Le
            # registre des consentements, lui, est en ajout seul : preuve légale conservée
            prospects = await _related_prospects(session, organization)
            await session.execute(
                text("DELETE FROM diagnostic_sessions WHERE id = ANY(:ids)"), {"ids": prospects}
            )
            await session.execute(delete(Organization).where(Organization.id == organization_id))

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
