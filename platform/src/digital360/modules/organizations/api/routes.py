import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request, status

from digital360.core.actor import Actor
from digital360.core.csrf import require_csrf
from digital360.core.errors import AppError
from digital360.core.pagination import PageParams, page_params
from digital360.core.permissions import Permission, Principal
from digital360.modules.identity.api.dependencies import (
    CurrentPrincipal,
    OrganizationAccess,
    require_org_permission,
    require_staff_permission,
)
from digital360.modules.organizations.api.schemas import (
    MemberList,
    MemberOut,
    OrganizationCreate,
    OrganizationOut,
    OrganizationPage,
    OrganizationUpdate,
)
from digital360.modules.organizations.application.service import OrganizationService
from digital360.modules.organizations.infrastructure.models import OrganizationStatus

logger = logging.getLogger("digital360.organizations")

router = APIRouter(tags=["organizations"])
admin_router = APIRouter(prefix="/admin", tags=["admin"])


def get_organization_service(request: Request) -> OrganizationService:
    service: OrganizationService = request.app.state.organization_service
    return service


Service = Annotated[OrganizationService, Depends(get_organization_service)]


@router.post(
    "/orgs",
    status_code=status.HTTP_201_CREATED,
    response_model=OrganizationOut,
    dependencies=[Depends(require_csrf)],
)
async def create_organization(
    body: OrganizationCreate,
    principal: CurrentPrincipal,
    service: Service,
    request: Request,
    x_diagnostic_token: Annotated[str | None, Header(max_length=100)] = None,
) -> OrganizationOut:
    diagnostic = None
    if body.diagnostic_id is not None:
        if not x_diagnostic_token:
            raise AppError("NOT_FOUND", "Diagnostic introuvable.", status=404)
        diagnostic = (body.diagnostic_id, x_diagnostic_token)
    values = body.model_dump(exclude={"diagnostic_id", "referral_code"})
    organization = await service.create(principal, values, diagnostic=diagnostic)
    if body.referral_code:
        # Parrainage Closer 3.0 : ne bloque jamais l'inscription
        try:
            await request.app.state.closer_service.attach_by_code(
                organization.id, principal.user_id, body.referral_code
            )
        except Exception:
            logger.exception("parrainage Closer 3.0 non enregistré")
    return OrganizationOut.model_validate(organization)


@router.get("/orgs/{org_id}", response_model=OrganizationOut)
async def get_organization(
    access: Annotated[
        OrganizationAccess, Depends(require_org_permission(Permission.ORGANIZATION_READ))
    ],
    service: Service,
) -> OrganizationOut:
    return OrganizationOut.model_validate(await service.get(access.tenant))


@router.patch(
    "/orgs/{org_id}", response_model=OrganizationOut, dependencies=[Depends(require_csrf)]
)
async def update_organization(
    body: OrganizationUpdate,
    access: Annotated[
        OrganizationAccess, Depends(require_org_permission(Permission.ORGANIZATION_UPDATE))
    ],
    service: Service,
) -> OrganizationOut:
    organization = await service.update(
        access.tenant, Actor.user(access.principal.user_id), body.model_dump(exclude_unset=True)
    )
    return OrganizationOut.model_validate(organization)


@router.get("/orgs/{org_id}/members", response_model=MemberList)
async def list_organization_members(
    access: Annotated[
        OrganizationAccess, Depends(require_org_permission(Permission.ORGANIZATION_READ))
    ],
    service: Service,
) -> MemberList:
    members = await service.members(access.tenant)
    return MemberList(data=[MemberOut(**vars(member)) for member in members])


@admin_router.get("/organizations", response_model=OrganizationPage)
async def admin_list_organizations(
    _: Annotated[Principal, Depends(require_staff_permission(Permission.ORGANIZATION_READ))],
    service: Service,
    params: Annotated[PageParams, Depends(page_params)],
    status_filter: Annotated[OrganizationStatus | None, Query(alias="status")] = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
) -> OrganizationPage:
    organizations, page = await service.admin_list(params, status=status_filter, query=q)
    return OrganizationPage(
        data=[OrganizationOut.model_validate(item) for item in organizations], page=page
    )


@admin_router.get("/organizations/{organization_id}", response_model=OrganizationOut)
async def admin_get_organization(
    organization_id: uuid.UUID,
    _: Annotated[Principal, Depends(require_staff_permission(Permission.ORGANIZATION_READ))],
    service: Service,
) -> OrganizationOut:
    return OrganizationOut.model_validate(await service.admin_get(organization_id))
