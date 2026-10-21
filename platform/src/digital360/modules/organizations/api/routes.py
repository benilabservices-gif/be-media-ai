import logging
import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request, status
from pydantic import BaseModel, ConfigDict, EmailStr, StringConstraints

from digital360.core.actor import Actor
from digital360.core.csrf import require_csrf
from digital360.core.errors import AppError
from digital360.core.pagination import PageParams, page_params
from digital360.core.permissions import ClientRole, Permission, Principal
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
from digital360.modules.organizations.application.team import (
    AcceptedInvitation,
    InvitationView,
    TeamService,
)
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


class DeletionPreviewOut(BaseModel):
    organization_id: uuid.UUID
    commercial_name: str
    members: int
    diagnostics: int
    payments: int
    website_projects: int
    purchase_requests: int
    # Prospects jamais rattachés qui concernent l'entreprise (même nom, e-mail ou numéro)
    prospects: int


class DeletionIn(BaseModel):
    # Le nom commercial, retapé par l'administrateur
    confirm_name: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
    ]
    # Obligatoire si l'entreprise a des paiements (chiffre d'affaires et reçus supprimés)
    delete_payments: bool = False


OrgDeleter = Annotated[Principal, Depends(require_staff_permission(Permission.ORGANIZATION_DELETE))]


@admin_router.get(
    "/organizations/{organization_id}/deletion-preview", response_model=DeletionPreviewOut
)
async def admin_deletion_preview(
    organization_id: uuid.UUID, _: OrgDeleter, service: Service
) -> DeletionPreviewOut:
    """Ce qui sera supprimé avec l'entreprise (à afficher dans la confirmation)."""
    return DeletionPreviewOut.model_validate(await service.admin_deletion_preview(organization_id))


@admin_router.post(
    "/organizations/{organization_id}/delete",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_csrf)],
)
async def admin_delete_organization(
    organization_id: uuid.UUID, body: DeletionIn, principal: OrgDeleter, service: Service
) -> None:
    """Suppression définitive (ADMIN). 422 CONFIRMATION_MISMATCH, 409 HAS_PAYMENTS."""
    await service.admin_delete(
        organization_id,
        principal.user_id,
        confirm_name=body.confirm_name,
        delete_payments=body.delete_payments,
    )


# ── Équipe : invitations et membres ──


def get_team_service(request: Request) -> TeamService:
    service: TeamService = request.app.state.team_service
    return service


Team = Annotated[TeamService, Depends(get_team_service)]
TeamManager = Annotated[
    OrganizationAccess, Depends(require_org_permission(Permission.MEMBERSHIP_MANAGE))
]


class InvitationIn(BaseModel):
    email: EmailStr
    # Responsable : achète et gère tout ; membre : consulte l'espace
    role: ClientRole = ClientRole.CLIENT_MEMBER


class InvitationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    role: ClientRole
    status: str
    expires_at: datetime | None
    created_at: datetime


class InvitationList(BaseModel):
    data: list[InvitationOut]


class AcceptInvitationIn(BaseModel):
    token: Annotated[str, StringConstraints(min_length=10, max_length=200)]


class AcceptedInvitationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    organization_id: uuid.UUID
    organization_name: str
    role: ClientRole


def _invitation_out(view: InvitationView) -> InvitationOut:
    return InvitationOut.model_validate(view)


@router.post(
    "/orgs/{org_id}/invitations",
    status_code=status.HTTP_201_CREATED,
    response_model=InvitationOut,
    dependencies=[Depends(require_csrf)],
)
async def invite_member(body: InvitationIn, access: TeamManager, team: Team) -> InvitationOut:
    """Invite un collaborateur par e-mail (réinviter la même adresse renvoie un nouveau lien)."""
    view = await team.invite(
        access.tenant, access.principal.user_id, email=str(body.email), role=body.role
    )
    return _invitation_out(view)


@router.get("/orgs/{org_id}/invitations", response_model=InvitationList)
async def list_invitations(access: TeamManager, team: Team) -> InvitationList:
    return InvitationList(
        data=[_invitation_out(item) for item in await team.pending(access.tenant)]
    )


@router.delete(
    "/orgs/{org_id}/invitations/{invitation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_csrf)],
)
async def cancel_invitation(invitation_id: uuid.UUID, access: TeamManager, team: Team) -> None:
    await team.cancel(access.tenant, access.principal.user_id, invitation_id)


@router.delete(
    "/orgs/{org_id}/members/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_csrf)],
)
async def remove_member(user_id: uuid.UUID, access: TeamManager, team: Team) -> None:
    """Retire un membre (409 LAST_OWNER : l'entreprise garde au moins un responsable)."""
    await team.remove_member(access.tenant, access.principal.user_id, user_id)


@router.post(
    "/invitations/accept",
    response_model=AcceptedInvitationOut,
    dependencies=[Depends(require_csrf)],
)
async def accept_invitation(
    body: AcceptInvitationIn, principal: CurrentPrincipal, team: Team
) -> AcceptedInvitationOut:
    """Lien de l'e-mail d'invitation, une fois connecté avec l'adresse invitée."""
    accepted: AcceptedInvitation = await team.accept(principal.user_id, body.token)
    return AcceptedInvitationOut.model_validate(accepted)
