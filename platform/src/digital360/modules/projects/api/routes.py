import uuid
from datetime import date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, StringConstraints

from digital360.core.csrf import require_csrf
from digital360.core.pagination import PageInfo, PageParams, page_params
from digital360.core.permissions import Permission, Principal
from digital360.modules.identity.api.dependencies import (
    OrganizationAccess,
    require_org_permission,
    require_staff_permission,
)
from digital360.modules.projects.application.service import ProjectService, ProjectView
from digital360.modules.projects.domain import offer
from digital360.modules.projects.domain.offer import Brief, RevisionCategory
from digital360.modules.projects.infrastructure.models import ProjectStatus

router = APIRouter(tags=["projects"])
admin_router = APIRouter(prefix="/admin", tags=["admin"])


def get_project_service(request: Request) -> ProjectService:
    service: ProjectService = request.app.state.project_service
    return service


Service = Annotated[ProjectService, Depends(get_project_service)]
Reader = Annotated[
    OrganizationAccess, Depends(require_org_permission(Permission.WEBSITE_PROJECT_READ))
]
Owner = Annotated[
    OrganizationAccess, Depends(require_org_permission(Permission.WEBSITE_PROJECT_CLIENT_REVIEW))
]
TeamReader = Annotated[
    Principal, Depends(require_staff_permission(Permission.WEBSITE_PROJECT_READ))
]
TeamWriter = Annotated[
    Principal, Depends(require_staff_permission(Permission.WEBSITE_PROJECT_TRANSITION))
]


# ── Schémas ──


class ChoiceOut(BaseModel):
    key: str
    name: str
    description: str


class OfferOut(BaseModel):
    """Le cadre de l'offre, à afficher tel quel : le client sait ce qu'il peut demander."""

    pitch: str
    pages: list[str]
    included: list[str]
    not_included: list[str]
    templates: list[ChoiceOut]
    palettes: list[ChoiceOut]
    max_services: int
    delivery_business_days: int
    max_revision_rounds: int
    revision_categories: dict[str, str]


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    organization_name: str
    status: ProjectStatus
    brief: dict[str, Any]
    # Champs encore requis avant de pouvoir envoyer le brief
    missing_fields: list[str]
    scope_accepted_at: datetime | None
    brief_submitted_at: datetime | None
    due_on: date | None
    preview_url: str | None
    live_url: str | None
    revisions_used: int
    revisions_left: int
    revision_requests: list[dict[str, Any]]
    # EDIT_BRIEF et les étapes que cet utilisateur peut déclencher
    available_actions: list[str]
    created_at: datetime
    updated_at: datetime


class ClientProjectOut(ProjectOut):
    offer: OfferOut


class SubmitRequest(BaseModel):
    accept_scope: bool


class RevisionItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: RevisionCategory
    page: Annotated[str, StringConstraints(pattern="^(" + "|".join(offer.PAGES) + ")$")]
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=500)]


class RevisionRequest(BaseModel):
    items: Annotated[list[RevisionItem], Field(min_length=1, max_length=offer.MAX_REVISION_ITEMS)]


class ProjectPage(BaseModel):
    data: list[ProjectOut]
    page: PageInfo


class TransitionRequest(BaseModel):
    target: ProjectStatus
    preview_url: HttpUrl | None = None
    live_url: HttpUrl | None = None
    reason: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] | None = None


OFFER = OfferOut(
    pitch=offer.PITCH,
    pages=list(offer.PAGES),
    included=list(offer.INCLUDED),
    not_included=list(offer.NOT_INCLUDED),
    templates=[ChoiceOut(**vars(item)) for item in offer.TEMPLATES],
    palettes=[ChoiceOut(**vars(item)) for item in offer.PALETTES],
    max_services=offer.MAX_SERVICES,
    delivery_business_days=offer.DELIVERY_BUSINESS_DAYS,
    max_revision_rounds=offer.MAX_REVISION_ROUNDS,
    revision_categories={key.value: label for key, label in offer.REVISION_CATEGORY_LABELS.items()},
)


def _client_out(view: ProjectView) -> ClientProjectOut:
    return ClientProjectOut(**ProjectOut.model_validate(view).model_dump(), offer=OFFER)


# ── Client ──


@router.get("/orgs/{org_id}/website-project", response_model=ClientProjectOut)
async def get_project(access: Reader, service: Service) -> ClientProjectOut:
    return _client_out(await service.get(access.tenant, access.principal))


@router.put(
    "/orgs/{org_id}/website-project/brief",
    response_model=ClientProjectOut,
    dependencies=[Depends(require_csrf)],
)
async def save_brief(body: Brief, access: Owner, service: Service) -> ClientProjectOut:
    """Enregistre le brouillon (partiel accepté, mais toujours dans le cadre de l'offre)."""
    return _client_out(await service.save_brief(access.tenant, access.principal, body))


@router.post(
    "/orgs/{org_id}/website-project/submit",
    response_model=ClientProjectOut,
    dependencies=[Depends(require_csrf)],
)
async def submit_brief(body: SubmitRequest, access: Owner, service: Service) -> ClientProjectOut:
    """Brief complet et périmètre accepté : la production démarre, la date de livraison est fixée."""
    view = await service.submit(access.tenant, access.principal, accept_scope=body.accept_scope)
    return _client_out(view)


@router.post(
    "/orgs/{org_id}/website-project/revision",
    response_model=ClientProjectOut,
    dependencies=[Depends(require_csrf)],
)
async def request_revision(
    body: RevisionRequest, access: Owner, service: Service
) -> ClientProjectOut:
    items = [item.model_dump(mode="json") for item in body.items]
    return _client_out(await service.request_revision(access.tenant, access.principal, items))


@router.post(
    "/orgs/{org_id}/website-project/approve",
    response_model=ClientProjectOut,
    dependencies=[Depends(require_csrf)],
)
async def approve(access: Owner, service: Service) -> ClientProjectOut:
    return _client_out(await service.approve(access.tenant, access.principal))


# ── Équipe ──


@admin_router.get("/website-projects", response_model=ProjectPage)
async def admin_list(
    principal: TeamReader,
    service: Service,
    params: Annotated[PageParams, Depends(page_params)],
    status: ProjectStatus | None = None,
) -> ProjectPage:
    views, page = await service.admin_list(principal, params, status=status)
    return ProjectPage(data=[ProjectOut.model_validate(view) for view in views], page=page)


@admin_router.get("/website-projects/{project_id}", response_model=ProjectOut)
async def admin_get(project_id: uuid.UUID, principal: TeamReader, service: Service) -> ProjectOut:
    return ProjectOut.model_validate(await service.admin_get(project_id, principal))


@admin_router.post(
    "/website-projects/{project_id}/transition",
    response_model=ProjectOut,
    dependencies=[Depends(require_csrf)],
)
async def admin_transition(
    project_id: uuid.UUID, body: TransitionRequest, principal: TeamWriter, service: Service
) -> ProjectOut:
    view = await service.admin_transition(
        project_id,
        principal,
        target=body.target,
        preview_url=str(body.preview_url) if body.preview_url else None,
        live_url=str(body.live_url) if body.live_url else None,
        reason=body.reason or None,
    )
    return ProjectOut.model_validate(view)
