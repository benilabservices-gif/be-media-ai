"""Projet Digital Start : brief encadré, production, relecture, une série de corrections, mise en ligne.

Étapes (machine à états `website_project`, historique et audit dans core.workflow) :

    BRIEF_PENDING → IN_PRODUCTION     le client envoie un brief complet et accepte le périmètre
    IN_PRODUCTION → CLIENT_REVIEW     l'équipe publie la première version (lien de préversion)
    CLIENT_REVIEW → REVISION          le client demande ses corrections de contenu (une fois)
    REVISION → CLIENT_REVIEW          l'équipe publie la version corrigée
    CLIENT_REVIEW → APPROVED          le client valide
    APPROVED → LIVE                   l'équipe met en ligne sur le domaine
    (étapes actives) → CANCELLED      ADMIN ou MANAGER, avec un motif
"""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from digital360.core.actor import Actor
from digital360.core.errors import AppError
from digital360.core.jobs import JobRegistry
from digital360.core.pagination import PageInfo, PageParams, build_page_info
from digital360.core.permissions import Permission, Principal
from digital360.core.state_machine import StateMachine, TransitionContext, transition
from digital360.core.tenancy import TenantContext, staff_transaction, tenant_transaction
from digital360.core.workflow import apply_transition
from digital360.modules.organizations.infrastructure.models import Organization
from digital360.modules.projects.domain import offer
from digital360.modules.projects.domain.offer import Brief
from digital360.modules.projects.infrastructure.models import ProjectStatus, WebsiteProject

P = ProjectStatus
CLIENT = Permission.WEBSITE_PROJECT_CLIENT_REVIEW.value
TEAM = Permission.WEBSITE_PROJECT_TRANSITION.value
# Annuler engage la relation commerciale : réservé à qui peut vendre (ADMIN, MANAGER)
CANCEL = Permission.ORDER_CREATE.value
ACTIVE_STATUSES = (P.BRIEF_PENDING, P.IN_PRODUCTION, P.CLIENT_REVIEW, P.REVISION, P.APPROVED)


def _brief_complete(project: WebsiteProject, _: TransitionContext) -> list[str]:
    missing = offer.missing_fields(Brief.model_validate(project.brief))
    return [f"Champ du brief à compléter : {name}" for name in missing]


def _scope_accepted(project: WebsiteProject, _: TransitionContext) -> list[str]:
    return [] if project.scope_accepted_at else ["Le périmètre de l'offre doit être accepté"]


def _has_preview(project: WebsiteProject, _: TransitionContext) -> list[str]:
    return [] if project.preview_url else ["Lien de préversion manquant"]


def _has_live_url(project: WebsiteProject, _: TransitionContext) -> list[str]:
    return [] if project.live_url else ["Adresse du site en ligne manquante"]


def _revision_left(project: WebsiteProject, _: TransitionContext) -> list[str]:
    if project.revisions_used >= offer.MAX_REVISION_ROUNDS:
        return ["La série de corrections incluse a déjà été utilisée"]
    return []


MACHINE: StateMachine[ProjectStatus, WebsiteProject] = StateMachine(
    "website_project",
    [
        transition(
            P.BRIEF_PENDING,
            P.IN_PRODUCTION,
            permissions=[CLIENT],
            guards=[_brief_complete, _scope_accepted],
        ),
        transition(P.IN_PRODUCTION, P.CLIENT_REVIEW, permissions=[TEAM], guards=[_has_preview]),
        transition(P.CLIENT_REVIEW, P.REVISION, permissions=[CLIENT], guards=[_revision_left]),
        transition(P.REVISION, P.CLIENT_REVIEW, permissions=[TEAM], guards=[_has_preview]),
        transition(P.CLIENT_REVIEW, P.APPROVED, permissions=[CLIENT]),
        transition(P.APPROVED, P.LIVE, permissions=[TEAM], guards=[_has_live_url]),
        transition(ACTIVE_STATUSES, P.CANCELLED, permissions=[CANCEL]),
    ],
)


@dataclass(frozen=True)
class ProjectView:
    id: uuid.UUID
    organization_id: uuid.UUID
    organization_name: str
    status: str
    brief: dict[str, Any]
    missing_fields: list[str]
    scope_accepted_at: datetime | None
    brief_submitted_at: datetime | None
    due_on: date | None
    preview_url: str | None
    live_url: str | None
    revisions_used: int
    revisions_left: int
    revision_requests: list[dict[str, Any]]
    created_at: datetime
    updated_at: datetime
    # Actions possibles pour l'utilisateur qui consulte (l'UI n'affiche que celles-là)
    available_actions: list[str] = field(default_factory=list)


def _view(
    project: WebsiteProject, organization_name: str, context: TransitionContext
) -> ProjectView:
    current = ProjectStatus(project.status)
    # Une action bloquée reste proposée si l'utilisateur la débloque en la déclenchant : saisir
    # le lien de préversion, l'adresse du site, compléter le brief. Une correction déjà utilisée,
    # elle, ne se débloque pas : l'action disparaît.
    unlocked_by_input = (P.IN_PRODUCTION, P.CLIENT_REVIEW, P.LIVE)
    actions = [
        str(item.target)
        for item in MACHINE.available(current, project, context)
        if not item.blocked_by or item.target in unlocked_by_input
    ]
    if current is P.BRIEF_PENDING and CLIENT in context.permissions:
        actions.insert(0, "EDIT_BRIEF")
    return ProjectView(
        id=project.id,
        organization_id=project.organization_id,
        organization_name=organization_name,
        status=project.status,
        brief=project.brief,
        missing_fields=offer.missing_fields(Brief.model_validate(project.brief)),
        scope_accepted_at=project.scope_accepted_at,
        brief_submitted_at=project.brief_submitted_at,
        due_on=project.due_on,
        preview_url=project.preview_url,
        live_url=project.live_url,
        revisions_used=project.revisions_used,
        revisions_left=max(offer.MAX_REVISION_ROUNDS - project.revisions_used, 0),
        revision_requests=project.revision_requests,
        created_at=project.created_at,
        updated_at=project.updated_at,
        available_actions=actions,
    )


def _client_context(principal: Principal, organization_id: uuid.UUID) -> TransitionContext:
    permissions = principal.client_permissions(organization_id) | principal.staff_permissions()
    return TransitionContext(
        actor=Actor.user(principal.user_id),
        permissions=frozenset(item.value for item in permissions),
    )


def _staff_context(principal: Principal) -> TransitionContext:
    return TransitionContext(
        actor=Actor.user(principal.user_id),
        permissions=frozenset(item.value for item in principal.staff_permissions()),
    )


def prefilled_brief(organization: Organization) -> dict[str, Any]:
    """Brief de départ repris de la fiche entreprise ; une valeur hors cadre est ignorée."""
    candidates: dict[str, Any] = {
        "business_name": organization.commercial_name,
        "activity": organization.description,
        "city": organization.city,
        "address": organization.address,
        "phone": organization.phone,
        "whatsapp": organization.whatsapp,
        "email": organization.email,
        "desired_domain": offer.clean_domain(organization.website),
    }
    kept: dict[str, Any] = {}
    for key, value in candidates.items():
        if value in (None, ""):
            continue
        try:
            Brief.model_validate({key: value})
        except ValidationError:
            continue
        kept[key] = value
    return Brief.model_validate(kept).model_dump(mode="json", exclude_none=True)


def _not_found() -> AppError:
    return AppError("NOT_FOUND", "Aucun projet de site pour cette entreprise.", status=404)


class ProjectService:
    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession], registry: JobRegistry
    ) -> None:
        self._session_factory = session_factory
        self._registry = registry

    # ── Côté client ──

    async def get(self, context: TenantContext, principal: Principal) -> ProjectView:
        async with tenant_transaction(self._session_factory, context) as session:
            project = await self._current(session, context.organization_id)
            return await self._to_view(
                session, project, _client_context(principal, context.organization_id)
            )

    async def save_brief(
        self, context: TenantContext, principal: Principal, brief: Brief
    ) -> ProjectView:
        async with tenant_transaction(self._session_factory, context) as session:
            project = await self._current(session, context.organization_id, lock=True)
            if project.status != P.BRIEF_PENDING:
                raise AppError(
                    "BRIEF_LOCKED",
                    "La production a démarré : le brief ne peut plus être modifié.",
                    status=409,
                )
            project.brief = brief.model_dump(mode="json", exclude_none=True)
            await session.flush()
            return await self._to_view(
                session, project, _client_context(principal, context.organization_id)
            )

    async def submit(
        self, context: TenantContext, principal: Principal, *, accept_scope: bool
    ) -> ProjectView:
        if not accept_scope:
            raise AppError(
                "SCOPE_NOT_ACCEPTED",
                "Acceptez le périmètre de l'offre pour lancer la réalisation.",
                status=422,
                errors=[{"field": "accept_scope", "reason": "required"}],
            )
        transition_context = _client_context(principal, context.organization_id)
        async with tenant_transaction(self._session_factory, context) as session:
            project = await self._current(session, context.organization_id, lock=True)
            now = datetime.now(UTC)
            project.scope_accepted_at = now
            project.scope_accepted_by = principal.user_id
            await apply_transition(
                session,
                MACHINE,
                project,
                P.IN_PRODUCTION,
                transition_context,
                registry=self._registry,
            )
            project.brief_submitted_at = now
            project.due_on = offer.add_business_days(now.date(), offer.DELIVERY_BUSINESS_DAYS)
            await session.flush()
            return await self._to_view(session, project, transition_context)

    async def request_revision(
        self,
        context: TenantContext,
        principal: Principal,
        items: list[dict[str, str]],
    ) -> ProjectView:
        transition_context = _client_context(principal, context.organization_id)
        async with tenant_transaction(self._session_factory, context) as session:
            project = await self._current(session, context.organization_id, lock=True)
            await apply_transition(
                session,
                MACHINE,
                project,
                P.REVISION,
                transition_context,
                details={"items": len(items)},
                registry=self._registry,
            )
            project.revisions_used += 1
            project.revision_requests = [
                *project.revision_requests,
                {"submitted_at": datetime.now(UTC).isoformat(), "items": items},
            ]
            await session.flush()
            return await self._to_view(session, project, transition_context)

    async def approve(self, context: TenantContext, principal: Principal) -> ProjectView:
        transition_context = _client_context(principal, context.organization_id)
        async with tenant_transaction(self._session_factory, context) as session:
            project = await self._current(session, context.organization_id, lock=True)
            await apply_transition(
                session, MACHINE, project, P.APPROVED, transition_context, registry=self._registry
            )
            return await self._to_view(session, project, transition_context)

    # ── Côté équipe ──

    async def admin_list(
        self, principal: Principal, params: PageParams, *, status: ProjectStatus | None
    ) -> tuple[list[ProjectView], PageInfo]:
        statement = (
            select(WebsiteProject).order_by(WebsiteProject.id.desc()).limit(params.limit + 1)
        )
        if params.after is not None:
            statement = statement.where(WebsiteProject.id < params.after)
        if status is not None:
            statement = statement.where(WebsiteProject.status == status.value)
        async with staff_transaction(self._session_factory) as session:
            projects = list((await session.execute(statement)).scalars())
            kept, page = build_page_info([item.id for item in projects], params)
            context = _staff_context(principal)
            return [await self._to_view(session, item, context) for item in projects[:kept]], page

    async def admin_get(self, project_id: uuid.UUID, principal: Principal) -> ProjectView:
        async with staff_transaction(self._session_factory) as session:
            project = await session.get(WebsiteProject, project_id)
            if project is None:
                raise AppError("NOT_FOUND", "Projet introuvable.", status=404)
            return await self._to_view(session, project, _staff_context(principal))

    async def admin_transition(
        self,
        project_id: uuid.UUID,
        principal: Principal,
        *,
        target: ProjectStatus,
        preview_url: str | None,
        live_url: str | None,
        reason: str | None,
    ) -> ProjectView:
        context = _staff_context(principal)
        async with staff_transaction(self._session_factory) as session:
            project = (
                await session.execute(
                    select(WebsiteProject).where(WebsiteProject.id == project_id).with_for_update()
                )
            ).scalar_one_or_none()
            if project is None:
                raise AppError("NOT_FOUND", "Projet introuvable.", status=404)
            if target is P.CANCELLED and not reason:
                raise AppError(
                    "VALIDATION_ERROR",
                    "Indiquez le motif de l'annulation.",
                    status=422,
                    errors=[{"field": "reason", "reason": "required"}],
                )
            # Les liens sont posés avant la transition : ses gardes les vérifient
            if preview_url:
                project.preview_url = preview_url
            if live_url:
                project.live_url = live_url
            await apply_transition(
                session, MACHINE, project, target, context, reason=reason, registry=self._registry
            )
            await session.flush()
            return await self._to_view(session, project, context)

    # ── Interne ──

    @staticmethod
    async def _current(
        session: AsyncSession, organization_id: uuid.UUID, *, lock: bool = False
    ) -> WebsiteProject:
        statement = (
            select(WebsiteProject)
            .where(
                WebsiteProject.organization_id == organization_id,
                WebsiteProject.status != P.CANCELLED.value,
            )
            .order_by(WebsiteProject.id.desc())
            .limit(1)
        )
        if lock:
            statement = statement.with_for_update()
        project = (await session.execute(statement)).scalar_one_or_none()
        if project is None:
            raise _not_found()
        return project

    @staticmethod
    async def _to_view(
        session: AsyncSession, project: WebsiteProject, context: TransitionContext
    ) -> ProjectView:
        await session.refresh(project)
        name = await session.scalar(
            select(Organization.commercial_name).where(Organization.id == project.organization_id)
        )
        return _view(project, name or "", context)
